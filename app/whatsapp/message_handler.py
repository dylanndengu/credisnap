"""
Inbound WhatsApp message state machine.

Every inbound message is routed through handle_message(), which reads the
current user state from the DB and dispatches to the correct handler.

State machine:
┌─────────────────────────────────────────────────────────────────────┐
│  New number (no user row)                                           │
│    → create user (consent=FALSE), send POPIA consent request        │
├─────────────────────────────────────────────────────────────────────┤
│  User exists, consent=FALSE                                         │
│    body="YES" → grant consent, set onboarding_step=BUSINESS_NAME    │
│    anything else → resend consent request                           │
├─────────────────────────────────────────────────────────────────────┤
│  onboarding_step=BUSINESS_NAME                                      │
│    → save business_name, set step=TAX_REF                           │
│    → ask for SARS income tax reference                              │
├─────────────────────────────────────────────────────────────────────┤
│  onboarding_step=TAX_REF                                            │
│    body="SKIP" → skip, set step=DONE, send welcome                  │
│    anything else → save income_tax_ref, set step=DONE, send welcome │
├─────────────────────────────────────────────────────────────────────┤
│  onboarding_step=DONE, media attachment                             │
│    → create document row, download+upload to S3, run pipeline       │
│    → send "Processing your receipt..." acknowledgement              │
├─────────────────────────────────────────────────────────────────────┤
│  onboarding_step=DONE, text = "YES" / "CONFIRM"                     │
│    → find most recent DRAFT journal entry, post it                  │
│    → send "Confirmed and posted" message                            │
├─────────────────────────────────────────────────────────────────────┤
│  onboarding_step=DONE, text = "NO" / "REJECT"                       │
│    → find most recent DRAFT journal entry, mark FAILED              │
│    → send "Discarded" message                                       │
├─────────────────────────────────────────────────────────────────────┤
│  Anything else                                                       │
│    → send help message                                              │
└─────────────────────────────────────────────────────────────────────┘

POPIA note: no personal data is stored before consent is granted.
The user row is created with minimal data (phone number only) and
immediately flagged as consent=FALSE until explicit opt-in.
"""

from __future__ import annotations

import logging
from datetime import date, timezone, datetime
from uuid import uuid4, UUID

import asyncpg

from app.db.connection import get_pool
from app.whatsapp import twilio_client
from app.whatsapp import media_handler
from app.services.ocr import textract_parser
from app.services.categorisation import llm_categoriser
from app.services.ledger import journal_writer

import anthropic

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WhatsApp message templates
# ---------------------------------------------------------------------------

_MSG_CONSENT = (
    "Welcome to CrediSnap! 👋\n\n"
    "To use this service, we need your consent to store and process your "
    "financial documents. Your data is protected under the South African "
    "Protection of Personal Information Act (POPIA).\n\n"
    "We will:\n"
    "• Store your receipts securely on encrypted servers\n"
    "• Use your data only to generate your financial statements\n"
    "• Retain records for 5 years as required by SARS\n\n"
    "Reply *YES* to give consent and start uploading receipts, "
    "or *NO* to decline."
)

_MSG_CONSENT_GRANTED = (
    "Thank you! Let's set up your account. 🎉\n\n"
    "What is your business name?"
)

_MSG_ASK_TAX_REF = (
    "Got it! One more question — what is your SARS income tax reference number?\n\n"
    "This helps lenders verify your tax standing. Reply *SKIP* if you don't have one."
)

_MSG_ONBOARDING_DONE = (
    "You're all set! 🎉\n\n"
    "Send me a photo or PDF of any receipt or invoice and I'll "
    "automatically record it in your books.\n\n"
    "When you're ready to view your financial statements, type *REPORT*."
)

_MSG_CONSENT_DECLINED = (
    "No problem. Your number has not been stored. "
    "Reply *YES* at any time if you change your mind."
)

_MSG_PROCESSING = (
    "Got it! I'm processing your receipt now. "
    "I'll send you a summary in a moment. ⏳"
)

_MSG_UNSUPPORTED_MEDIA = (
    "Sorry, I can only read JPEG, PNG, or PDF files. "
    "Please send a clear photo or PDF of your receipt."
)

_MSG_NO_PENDING = (
    "There's nothing waiting for confirmation right now. "
    "Send me a receipt to get started."
)

_MSG_HELP = (
    "Here's what you can do:\n\n"
    "📎 *Send a photo or PDF* — record a receipt or invoice\n"
    "✅ *YES* — confirm a pending entry\n"
    "❌ *NO* — discard a pending entry\n"
    "📊 *REPORT* — request your latest financial statements"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_number(raw: str) -> str:
    """Strip 'whatsapp:' prefix Twilio prepends to phone numbers."""
    return raw.replace("whatsapp:", "").strip()


def _parse_confirmation(body: str) -> str | None:
    """Return 'YES', 'NO', or None based on the message body."""
    normalised = body.strip().upper()
    if normalised in ("YES", "Y", "CONFIRM", "JA"):
        return "YES"
    if normalised in ("NO", "N", "REJECT", "NEE"):
        return "NO"
    return None


# ---------------------------------------------------------------------------
# Sub-handlers
# ---------------------------------------------------------------------------

async def _ensure_user(conn: asyncpg.Connection, whatsapp_number: str) -> dict:
    """
    Return the user row, creating a minimal record if this number is new.
    The new record has popia_consent_given=FALSE — nothing is processed until consent.
    """
    row = await conn.fetchrow(
        "SELECT id, popia_consent_given, onboarding_step FROM users WHERE whatsapp_number = $1",
        whatsapp_number,
    )
    if row:
        return dict(row)

    # First contact — create a skeleton user, no consent yet
    user_id = await conn.fetchval(
        """
        INSERT INTO users (whatsapp_number, business_name, popia_consent_given)
        VALUES ($1, $2, FALSE)
        RETURNING id
        """,
        whatsapp_number,
        "Unknown Business",
    )
    log.info("Created new user %s for %s", user_id, whatsapp_number)
    return {"id": user_id, "popia_consent_given": False, "onboarding_step": None}


async def _grant_consent(conn: asyncpg.Connection, user_id: UUID) -> None:
    await conn.execute(
        """
        UPDATE users SET
            popia_consent_given   = TRUE,
            popia_consent_at      = NOW(),
            popia_consent_version = '1.0',
            data_retention_until  = (CURRENT_DATE + INTERVAL '7 years')::date,
            onboarding_step       = 'BUSINESS_NAME',
            updated_at            = NOW()
        WHERE id = $1
        """,
        user_id,
    )


async def _save_business_name(
    conn: asyncpg.Connection, user_id: UUID, business_name: str
) -> None:
    await conn.execute(
        """
        UPDATE users SET
            business_name   = $2,
            onboarding_step = 'TAX_REF',
            updated_at      = NOW()
        WHERE id = $1
        """,
        user_id,
        business_name.strip(),
    )


async def _save_tax_ref(
    conn: asyncpg.Connection, user_id: UUID, tax_ref: str | None
) -> None:
    """Save income tax reference (may be None if the user skipped) and mark onboarding done."""
    await conn.execute(
        """
        UPDATE users SET
            income_tax_ref  = $2,
            onboarding_step = 'DONE',
            updated_at      = NOW()
        WHERE id = $1
        """,
        user_id,
        tax_ref,
    )


async def _find_draft_entry(
    conn: asyncpg.Connection, user_id: UUID
) -> UUID | None:
    """Return the UUID of the user's most recent DRAFT journal entry, or None."""
    return await conn.fetchval(
        """
        SELECT id FROM journal_entries
        WHERE  user_id = $1 AND status = 'DRAFT'
        ORDER  BY created_at DESC
        LIMIT  1
        """,
        user_id,
    )


async def _post_draft_entry(conn: asyncpg.Connection, entry_id: UUID) -> str:
    """
    Post a DRAFT entry. Returns a human-readable summary for the confirmation message.
    The DB trigger validates balance — if it fails, the exception propagates.
    """
    row = await conn.fetchrow(
        "SELECT description, entry_date FROM journal_entries WHERE id = $1",
        entry_id,
    )
    await conn.execute(
        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
        entry_id,
    )
    return f"{row['description']} ({row['entry_date']})"


async def _handle_media_message(
    conn: asyncpg.Connection,
    user_id: UUID,
    from_number: str,
    form_data: dict,
) -> None:
    """Download the attachment, run the full pipeline, notify user."""
    media_url          = form_data.get("MediaUrl0", "")
    media_content_type = form_data.get("MediaContentType0", "")
    whatsapp_msg_id    = form_data.get("MessageSid", "")

    # Send immediate acknowledgement so the user knows something is happening
    twilio_client.send_whatsapp(from_number, _MSG_PROCESSING)

    # 1. Validate and download
    try:
        content, mime_type = await media_handler.download_media(media_url, media_content_type)
    except ValueError as exc:
        twilio_client.send_whatsapp(from_number, _MSG_UNSUPPORTED_MEDIA)
        log.warning("Unsupported media from %s: %s", from_number, exc)
        return

    # 2. Create document row (status=PENDING)
    document_id: UUID = await conn.fetchval(
        """
        INSERT INTO documents
            (user_id, s3_bucket, s3_key, mime_type, file_size_bytes,
             whatsapp_message_id, status)
        VALUES ($1, '', '', $2, $3, $4, 'PENDING')
        RETURNING id
        """,
        user_id,
        mime_type,
        len(content),
        whatsapp_msg_id,
    )

    # 3. Upload to S3
    bucket, key, etag = media_handler.upload_to_s3(content, mime_type, user_id, document_id)
    await conn.execute(
        """
        UPDATE documents
        SET s3_bucket = $2, s3_key = $3, s3_etag = $4, updated_at = NOW()
        WHERE id = $1
        """,
        document_id, bucket, key, etag,
    )

    # 4. Textract
    raw_textract = media_handler.analyze_expense(bucket, key)

    # 5. Full OCR → categorisation → ledger pipeline
    from app.pipeline import process_document
    entry_id = await process_document(document_id, raw_textract)

    # 6. Notify user with outcome
    entry_row = await conn.fetchrow(
        """
        SELECT je.description, je.status, je.ai_confidence,
               d.gross_amount, d.vendor_name
        FROM   journal_entries je
        JOIN   documents       d  ON d.id = je.document_id
        WHERE  je.id = $1
        """,
        entry_id,
    )

    if entry_row["status"] == "POSTED":
        msg = (
            f"✅ Recorded!\n\n"
            f"*{entry_row['vendor_name'] or 'Receipt'}* — "
            f"R{entry_row['gross_amount']:,.2f}\n"
            f"{entry_row['description']}\n\n"
            f"This has been posted to your books automatically."
        )
    else:
        msg = (
            f"📋 I've read your receipt from *{entry_row['vendor_name'] or 'Unknown vendor'}* "
            f"(R{entry_row['gross_amount']:,.2f}).\n\n"
            f"I'm not 100% confident in the categorisation. "
            f"Reply *YES* to confirm and post it, or *NO* to discard."
        )

    twilio_client.send_whatsapp(from_number, msg)


# ---------------------------------------------------------------------------
# Main entry point (called as a FastAPI BackgroundTask)
# ---------------------------------------------------------------------------

async def handle_message(form_data: dict) -> None:
    """
    Process a single inbound Twilio WhatsApp message end-to-end.

    This runs as a background task — the HTTP response has already been
    sent to Twilio by the time this executes.
    """
    from_raw     = form_data.get("From", "")
    from_number  = _normalise_number(from_raw)
    body         = form_data.get("Body", "").strip()
    num_media    = int(form_data.get("NumMedia", 0))

    if not from_number:
        log.error("Received message with no From field: %s", form_data)
        return

    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await _ensure_user(conn, from_number)
        user_id: UUID = user["id"]

        # ------------------------------------------------------------------
        # POPIA consent gate
        # ------------------------------------------------------------------
        if not user["popia_consent_given"]:
            confirmation = _parse_confirmation(body)
            if confirmation == "YES":
                await _grant_consent(conn, user_id)
                twilio_client.send_whatsapp(from_number, _MSG_CONSENT_GRANTED)
            elif confirmation == "NO":
                # Delete the skeleton user row — they declined
                await conn.execute("DELETE FROM users WHERE id = $1", user_id)
                twilio_client.send_whatsapp(from_number, _MSG_CONSENT_DECLINED)
            else:
                twilio_client.send_whatsapp(from_number, _MSG_CONSENT)
            return

        # ------------------------------------------------------------------
        # Onboarding flow
        # ------------------------------------------------------------------
        onboarding_step = user.get("onboarding_step")

        if onboarding_step == "BUSINESS_NAME":
            if not body:
                twilio_client.send_whatsapp(from_number, "Please enter your business name.")
                return
            await _save_business_name(conn, user_id, body)
            twilio_client.send_whatsapp(from_number, _MSG_ASK_TAX_REF)
            return

        if onboarding_step == "TAX_REF":
            tax_ref = None if body.strip().upper() == "SKIP" else body.strip()
            await _save_tax_ref(conn, user_id, tax_ref)
            twilio_client.send_whatsapp(from_number, _MSG_ONBOARDING_DONE)
            return

        # ------------------------------------------------------------------
        # Fully onboarded user — route by message type
        # ------------------------------------------------------------------
        if num_media > 0:
            try:
                await _handle_media_message(conn, user_id, from_number, form_data)
            except Exception as exc:
                log.exception("Media processing failed for %s", from_number)
                twilio_client.send_whatsapp(
                    from_number,
                    "Sorry, something went wrong processing that receipt. "
                    "Please try sending it again.",
                )
            return

        # Text message — check for confirmation keywords
        confirmation = _parse_confirmation(body)
        if confirmation in ("YES", "NO"):
            draft_entry_id = await _find_draft_entry(conn, user_id)
            if draft_entry_id is None:
                twilio_client.send_whatsapp(from_number, _MSG_NO_PENDING)
                return

            if confirmation == "YES":
                try:
                    summary = await _post_draft_entry(conn, draft_entry_id)
                    twilio_client.send_whatsapp(
                        from_number,
                        f"✅ Posted: {summary}",
                    )
                except Exception as exc:
                    log.exception("Failed to post entry %s", draft_entry_id)
                    twilio_client.send_whatsapp(
                        from_number,
                        "Sorry, I couldn't post that entry. Please try again.",
                    )
            else:
                await conn.execute(
                    "UPDATE journal_entries SET status = 'DRAFT' WHERE id = $1",
                    draft_entry_id,
                )
                # Mark source document as FAILED so it can be retried or ignored
                await conn.execute(
                    """
                    UPDATE documents SET status = 'REJECTED', updated_at = NOW()
                    WHERE id = (
                        SELECT document_id FROM journal_entries WHERE id = $1
                    )
                    """,
                    draft_entry_id,
                )
                await conn.execute(
                    "DELETE FROM journal_entries WHERE id = $1",
                    draft_entry_id,
                )
                twilio_client.send_whatsapp(from_number, "🗑️ Discarded. Send another receipt when ready.")
            return

        # Fall-through: unrecognised text
        twilio_client.send_whatsapp(from_number, _MSG_HELP)
