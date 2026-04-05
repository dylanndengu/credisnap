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
from app.services.reporting import statement_generator, report_orchestrator
from app.services.vision import receipt_checker

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

_MSG_REJECTION_OPTIONS = (
    "No problem. What would you like to do?\n\n"
    "*1* — Wrong category (I'll re-categorise it)\n"
    "*2* — Wrong amount (I'll discard it — please re-upload)\n"
    "*3* — Not a business expense (discard)"
)

_MSG_ASK_CATEGORY_HINT = (
    "What is this expense for? Describe it in a few words\n"
    "(e.g. 'office stationery', 'fuel for delivery', 'client lunch')"
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
        "SELECT id, popia_consent_given, onboarding_step, financial_year_end_month, conversation_state FROM users WHERE whatsapp_number = $1",
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
    return {"id": user_id, "popia_consent_given": False, "onboarding_step": None, "financial_year_end_month": 2, "conversation_state": None}


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
    # Seed the Chart of Accounts from the standard template
    await conn.execute(
        """
        INSERT INTO accounts (user_id, code, name, account_type, normal_balance, parent_id, ifrs_line_item)
        SELECT
            $1,
            t.code, t.name, t.account_type, t.normal_balance,
            NULL,
            t.ifrs_line_item
        FROM account_templates t
        ON CONFLICT (user_id, code) DO NOTHING
        """,
        user_id,
    )
    log.info("Seeded Chart of Accounts for user %s", user_id)


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


async def _delete_draft_entry(conn: asyncpg.Connection, entry_id: UUID) -> None:
    """Delete a DRAFT journal entry and its associated vat_entries (RESTRICT FK requires explicit delete)."""
    await conn.execute("DELETE FROM vat_entries WHERE journal_entry_id = $1", entry_id)
    await conn.execute("DELETE FROM journal_entries WHERE id = $1", entry_id)


async def _recategorise_draft(
    conn: asyncpg.Connection,
    user_id: UUID,
    entry_id: UUID,
    hint: str,
) -> UUID:
    """
    Delete the existing DRAFT entry and re-run categorisation with the user's hint.
    Returns the new journal entry UUID.
    """
    import json as _json
    from app.services.ocr import textract_parser
    from app.services.categorisation import llm_categoriser
    from app.services.ledger import journal_writer

    # Get the source document
    row = await conn.fetchrow(
        "SELECT document_id FROM journal_entries WHERE id = $1",
        entry_id,
    )
    document_id = row["document_id"]

    # Fetch raw OCR JSON
    ocr_row = await conn.fetchrow(
        "SELECT ocr_raw_json FROM documents WHERE id = $1",
        document_id,
    )
    raw_json = ocr_row["ocr_raw_json"]
    if isinstance(raw_json, str):
        raw_json = _json.loads(raw_json)

    # Delete old draft entry
    await _delete_draft_entry(conn, entry_id)

    # Re-parse and re-categorise with hint
    expense_raw = textract_parser.parse(raw_json)
    valid_codes = {r["code"] for r in await conn.fetch(
        "SELECT code FROM accounts WHERE user_id = $1 AND is_active = TRUE",
        user_id,
    )}
    expense_categorised = llm_categoriser.categorise(
        expense_raw, valid_codes, hint=hint
    )

    return await journal_writer.write(conn, user_id, document_id, expense_categorised)


async def _set_conversation_state(
    conn: asyncpg.Connection, user_id: UUID, state: str | None
) -> None:
    await conn.execute(
        "UPDATE users SET conversation_state = $2::conversation_state, updated_at = NOW() WHERE id = $1",
        user_id,
        state,
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

    # 1b. Vision check — reject if the image contains multiple receipts
    if receipt_checker.contains_multiple_receipts(content, mime_type):
        twilio_client.send_whatsapp(
            from_number,
            "📸 It looks like your photo contains more than one receipt.\n\n"
            "Please send one receipt at a time so I can record each one accurately."
        )
        log.info("Rejected multi-receipt image from %s", from_number)
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

    # Fetch the expense account lines for display (exclude Bank and VAT Input lines)
    category_rows = await conn.fetch(
        """
        SELECT a.name, a.code, jel.debit_amount
        FROM   journal_entry_lines jel
        JOIN   accounts            a ON a.id = jel.account_id
        WHERE  jel.journal_entry_id = $1
          AND  jel.debit_amount > 0
          AND  a.code NOT IN ('1020')
        ORDER  BY jel.debit_amount DESC
        """,
        entry_id,
    )

    if entry_row["status"] == "POSTED":
        category_lines = "\n".join(
            f"  • {r['name']} — R{r['debit_amount']:,.2f}" for r in category_rows
        )
        msg = (
            f"✅ Recorded!\n\n"
            f"*{entry_row['vendor_name'] or 'Receipt'}* — "
            f"R{entry_row['gross_amount']:,.2f}\n\n"
            f"{category_lines}\n\n"
            f"Posted to your books automatically."
        )
    else:
        category_lines = "\n".join(
            f"  • {r['name']} — R{r['debit_amount']:,.2f}" for r in category_rows
        )
        msg = (
            f"📋 *{entry_row['vendor_name'] or 'Unknown vendor'}* — "
            f"R{entry_row['gross_amount']:,.2f}\n\n"
            f"I've categorised this as:\n{category_lines}\n\n"
            f"Reply *YES* to confirm and post, or *NO* to discard."
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

        # ------------------------------------------------------------------
        # Multi-turn conversation states
        # ------------------------------------------------------------------
        conversation_state = user.get("conversation_state")

        if conversation_state == "AWAITING_REJECTION_REASON":
            draft_entry_id = await _find_draft_entry(conn, user_id)
            choice = body.strip()
            if choice == "1":
                await _set_conversation_state(conn, user_id, "AWAITING_CATEGORY_HINT")
                twilio_client.send_whatsapp(from_number, _MSG_ASK_CATEGORY_HINT)
            elif choice == "2":
                if draft_entry_id:
                    await conn.execute(
                        "UPDATE documents SET status = 'REJECTED', updated_at = NOW() "
                        "WHERE id = (SELECT document_id FROM journal_entries WHERE id = $1)",
                        draft_entry_id,
                    )
                    await _delete_draft_entry(conn, draft_entry_id)
                await _set_conversation_state(conn, user_id, None)
                twilio_client.send_whatsapp(
                    from_number,
                    "🗑️ Discarded. Please re-upload the receipt with a clearer photo."
                )
            elif choice == "3":
                if draft_entry_id:
                    await conn.execute(
                        "UPDATE documents SET status = 'REJECTED', updated_at = NOW() "
                        "WHERE id = (SELECT document_id FROM journal_entries WHERE id = $1)",
                        draft_entry_id,
                    )
                    await _delete_draft_entry(conn, draft_entry_id)
                await _set_conversation_state(conn, user_id, None)
                twilio_client.send_whatsapp(from_number, "🗑️ Discarded. Send another receipt when ready.")
            else:
                twilio_client.send_whatsapp(from_number, _MSG_REJECTION_OPTIONS)
            return

        if conversation_state == "AWAITING_CATEGORY_HINT":
            if not body:
                twilio_client.send_whatsapp(from_number, _MSG_ASK_CATEGORY_HINT)
                return
            draft_entry_id = await _find_draft_entry(conn, user_id)
            if draft_entry_id is None:
                await _set_conversation_state(conn, user_id, None)
                twilio_client.send_whatsapp(from_number, _MSG_NO_PENDING)
                return
            try:
                new_entry_id = await _recategorise_draft(conn, user_id, draft_entry_id, hint=body)
                await _set_conversation_state(conn, user_id, None)
                # Fetch and display the new categorisation
                entry_row = await conn.fetchrow(
                    "SELECT je.status, d.gross_amount, d.vendor_name "
                    "FROM journal_entries je JOIN documents d ON d.id = je.document_id "
                    "WHERE je.id = $1",
                    new_entry_id,
                )
                category_rows = await conn.fetch(
                    "SELECT a.name, jel.debit_amount FROM journal_entry_lines jel "
                    "JOIN accounts a ON a.id = jel.account_id "
                    "WHERE jel.journal_entry_id = $1 AND jel.debit_amount > 0 AND a.code != '1020' "
                    "ORDER BY jel.debit_amount DESC",
                    new_entry_id,
                )
                category_lines = "\n".join(
                    f"  • {r['name']} — R{r['debit_amount']:,.2f}" for r in category_rows
                )
                if entry_row["status"] == "POSTED":
                    twilio_client.send_whatsapp(
                        from_number,
                        f"✅ Re-categorised and posted!\n\n"
                        f"*{entry_row['vendor_name'] or 'Receipt'}* — R{entry_row['gross_amount']:,.2f}\n\n"
                        f"{category_lines}"
                    )
                else:
                    twilio_client.send_whatsapp(
                        from_number,
                        f"📋 *{entry_row['vendor_name'] or 'Receipt'}* — R{entry_row['gross_amount']:,.2f}\n\n"
                        f"New categorisation:\n{category_lines}\n\n"
                        f"Reply *YES* to confirm, or *NO* to try again."
                    )
            except Exception:
                log.exception("Re-categorisation failed for %s", from_number)
                await _set_conversation_state(conn, user_id, None)
                twilio_client.send_whatsapp(
                    from_number,
                    "Sorry, I couldn't re-categorise that. The entry has been discarded — please re-upload the receipt."
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
                except Exception:
                    log.exception("Failed to post entry %s", draft_entry_id)
                    twilio_client.send_whatsapp(
                        from_number,
                        "Sorry, I couldn't post that entry. Please try again.",
                    )
            else:
                # Ask what was wrong instead of silently discarding
                await _set_conversation_state(conn, user_id, "AWAITING_REJECTION_REASON")
                twilio_client.send_whatsapp(from_number, _MSG_REJECTION_OPTIONS)
            return

        # REPORT keyword — generate PDF financial report
        if body.strip().upper() == "REPORT":
            try:
                fy_end_month = user.get("financial_year_end_month") or 2
                twilio_client.send_whatsapp(
                    from_number,
                    "📊 Generating your financial report... this may take a moment."
                )
                result = await report_orchestrator.generate_and_deliver(
                    conn, user_id, fy_end_month
                )
                if result is None:
                    msg = (
                        "No posted transactions found for this period yet.\n\n"
                        "Upload a receipt and confirm it to see your financial statements."
                    )
                else:
                    msg = (
                        f"Your financial report is ready! 📊\n\n"
                        f"*{result.business_name}*\n"
                        f"Period: {result.from_date.strftime('%d %b %Y')} – "
                        f"{result.to_date.strftime('%d %b %Y')}\n\n"
                        f"Includes: Trial Balance, General Ledger, P&L, "
                        f"Balance Sheet, VAT201 Summary & Vendor Statements.\n\n"
                        f"Download your PDF (link expires in 24 hours):\n"
                        f"{result.presigned_url}"
                    )
            except Exception:
                log.exception("Failed to generate PDF report for %s", from_number)
                msg = "Sorry, I couldn't generate your report right now. Please try again."
            twilio_client.send_whatsapp(from_number, msg)
            return

        # Fall-through: unrecognised text
        twilio_client.send_whatsapp(from_number, _MSG_HELP)
