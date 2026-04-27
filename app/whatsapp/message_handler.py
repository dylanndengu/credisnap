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
│    → auto-post if confidence ≥ 0.85                                 │
│    POSTED → "✅ Recorded. Reply EDIT within 24 h if anything wrong" │
│    DRAFT  → show details → "Reply YES to confirm or NO if wrong"    │
├─────────────────────────────────────────────────────────────────────┤
│  conversation_state=AWAITING_DOCUMENT_TYPE                          │
│    body="EXPENSE" / "PURCHASE" → resume as PURCHASE                 │
│    body="INCOME"  / "SALE"     → resume as SALE                     │
│    → same POSTED/DRAFT display as above                             │
├─────────────────────────────────────────────────────────────────────┤
│  body="YES" (DRAFT exists) → post it → done                         │
├─────────────────────────────────────────────────────────────────────┤
│  body="NO" (DRAFT exists) → set pending_entry_id, AWAITING_EDIT_CHOICE │
│  body="EDIT" (any recent entry) → set pending_entry_id, AWAITING_EDIT_CHOICE │
├─────────────────────────────────────────────────────────────────────┤
│  AWAITING_EDIT_CHOICE                                               │
│    1 → AWAITING_CORRECT_AMOUNT  (amount wrong)                      │
│    2 → AWAITING_CATEGORY_HINT   (category wrong)                    │
│    3 → reverse entry → done     (not a business expense)            │
│    4 → AWAITING_CATEGORY_HINT   (something else — describe)         │
├─────────────────────────────────────────────────────────────────────┤
│  AWAITING_CORRECT_AMOUNT                                            │
│    → parse ZAR amount → reverse original + record correct amount    │
├─────────────────────────────────────────────────────────────────────┤
│  AWAITING_CATEGORY_HINT                                             │
│    → reverse original + re-categorise with hint → done              │
├─────────────────────────────────────────────────────────────────────┤
│  Anything else → send help message                                  │
└─────────────────────────────────────────────────────────────────────┘

POPIA note: no personal data is stored before consent is granted.
The user row is created with minimal data (phone number only) and
immediately flagged as consent=FALSE until explicit opt-in.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, timezone, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4, UUID

import asyncpg

from app.db.connection import get_pool
from app.whatsapp import twilio_client
from app.whatsapp import media_handler
from app.services.ocr import textract_parser
from app.services.categorisation import llm_categoriser
from app.services.ledger import journal_writer
from app.services.reporting import statement_generator, report_orchestrator, report_queries
from app.services.vision import receipt_checker

import anthropic

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-number rate limiter (in-memory, resets on restart)
# Prevents runaway Textract/LLM costs from a single misbehaving number.
# ---------------------------------------------------------------------------

_rate_buckets: dict[str, list[datetime]] = defaultdict(list)
_RATE_WINDOW  = timedelta(minutes=1)
_RATE_MAX     = 10  # messages per number per minute


def _is_rate_limited(number: str) -> bool:
    now    = datetime.utcnow()
    cutoff = now - _RATE_WINDOW
    bucket = _rate_buckets[number]
    # Drop timestamps outside the window
    _rate_buckets[number] = [t for t in bucket if t > cutoff]
    if len(_rate_buckets[number]) >= _RATE_MAX:
        return True
    _rate_buckets[number].append(now)
    return False


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

_MSG_ASK_PROVINCE = (
    "Which province do you operate in?\n\n"
    "*1* Gauteng\n"
    "*2* Western Cape\n"
    "*3* KwaZulu-Natal\n"
    "*4* Eastern Cape\n"
    "*5* Limpopo\n"
    "*6* Mpumalanga\n"
    "*7* North West\n"
    "*8* Free State\n"
    "*9* Northern Cape"
)

_PROVINCE_MAP = {
    "1": "Gauteng", "2": "Western Cape", "3": "KwaZulu-Natal",
    "4": "Eastern Cape", "5": "Limpopo", "6": "Mpumalanga",
    "7": "North West", "8": "Free State", "9": "Northern Cape",
}

_MSG_ASK_BUSINESS_TYPE = (
    "What type of business do you run?\n\n"
    "*1* Retail / Trading\n"
    "*2* Food & Catering\n"
    "*3* Professional Services (consulting, legal, accounting)\n"
    "*4* Construction & Trades\n"
    "*5* Transport & Logistics\n"
    "*6* Cleaning & Security\n"
    "*7* Health & Wellness\n"
    "*8* Agriculture\n"
    "*9* Manufacturing\n"
    "*10* Other"
)

_BUSINESS_TYPE_MAP = {
    "1": "Retail / Trading", "2": "Food & Catering",
    "3": "Professional Services", "4": "Construction & Trades",
    "5": "Transport & Logistics", "6": "Cleaning & Security",
    "7": "Health & Wellness", "8": "Agriculture",
    "9": "Manufacturing", "10": "Other",
}

_MSG_ASK_TAX_REF = (
    "Last one — what is your SARS income tax reference number?\n\n"
    "This helps lenders verify your tax standing. Reply *SKIP* if you don't have one yet."
)

_MSG_ONBOARDING_DONE = (
    "You're all set! 🎉\n\n"
    "Here's how to record your income and expenses:\n\n"
    "📎 *Send a photo or PDF* — I'll read any receipt or invoice\n"
    "💵 *CASH SALE* — record a sale when you have no receipt\n\n"
    "Type *REPORT* when you want your financial statements.\n"
    "Type *HELP* anytime to see all commands."
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

_MSG_ASK_CORRECT_AMOUNT = "What's the correct amount? (ZAR)\n\nE.g. *R 8 900* or *8900.00*"

_MSG_ASK_CATEGORY_HINT = (
    "What is this expense/income for? Describe it in a few words:\n"
    "(e.g. 'office stationery', 'fuel for delivery', 'consulting income')"
)

_MSG_NO_PENDING = (
    "There's nothing waiting for confirmation right now. "
    "Send me a receipt to get started."
)

_MSG_HELP = (
    "Here's what you can do:\n\n"
    "*Receipts & invoices*\n"
    "📎 Send a photo or PDF — I'll record it\n"
    "💵 *CASH SALE* — record a sale with no receipt\n"
    "✅ *YES* / ❌ *NO* — confirm or correct an entry\n"
    "✏️ *EDIT* — fix your last entry\n\n"
    "*Your books*\n"
    "📊 *BALANCE* — income vs expenses this month\n"
    "📋 *LAST* — show my last entry\n"
    "🏆 *TOP* — top categories this month\n"
    "⏳ *PENDING* — receipts awaiting review\n\n"
    "*Reports*\n"
    "📄 *REPORT* — full financial PDF\n"
    "📄 *REPORT 2025* — for a specific year"
)

_MSG_ASK_DOCUMENT_TYPE = (
    "I'm not sure whether this is an *expense* or an *income* document.\n\n"
    "Please reply:\n"
    "  *EXPENSE* — a receipt or invoice you paid (purchase)\n"
    "  *INCOME* — an invoice you issued to a customer (sale)"
)

_MSG_EDIT_MENU = (
    "What's wrong with this entry?\n\n"
    "{summary}\n\n"
    "*1* — Wrong type (recorded as expense but it's actually income, or vice versa)\n"
    "*2* — The amount is wrong\n"
    "*3* — The company or person name is wrong\n"
    "*4* — Wrong category (e.g. should be fuel, not stationery)\n"
    "*5* — Remove it completely"
)

_MSG_ASK_CORRECT_COUNTERPARTY = (
    "What is the correct company or person name for this entry?\n\n"
    "Just type the name as it should appear."
)

_MSG_ASK_CASH_SALE_DESCRIPTION = (
    "What did you sell?\n\n"
    "Describe it in a few words:\n"
    "(e.g. 'catering for 20 people', '3 plastic chairs', 'repaired a gate motor', 'consulting session')"
)

_MSG_ASK_CASH_SALE_AMOUNT = (
    "Got it. What was the total amount received? (ZAR)\n\n"
    "E.g. *R 1 500* or *1500*"
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


def _parse_zar_amount(text: str) -> Decimal | None:
    """Parse a ZAR amount from user input. Returns None if unparseable."""
    cleaned = re.sub(r"[Rr\s,]", "", text.strip())
    try:
        amount = Decimal(cleaned)
        if amount > 0:
            return amount
    except InvalidOperation:
        pass
    return None


def _fmt_date(d) -> str:
    if d is None:
        return "Unknown date"
    return f"{d.day} {d.strftime('%b %Y')}"


async def _entry_summary(conn: asyncpg.Connection, entry_id: UUID) -> str:
    """One-line summary of an entry: 'Shell Garage — R 890.00 (Fuel and Vehicle Exp.)'"""
    row = await conn.fetchrow(
        """
        SELECT d.vendor_name, d.gross_amount, d.document_date,
               d.document_type::text AS document_type
        FROM   journal_entries je
        JOIN   documents       d ON d.id = je.document_id
        WHERE  je.id = $1
        """,
        entry_id,
    )
    if row is None:
        return "Unknown entry"

    is_sale = (row["document_type"] or "PURCHASE") == "SALE"

    # Main category name
    if is_sale:
        cat_row = await conn.fetchrow(
            """
            SELECT a.name FROM journal_entry_lines jel
            JOIN accounts a ON a.id = jel.account_id
            WHERE jel.journal_entry_id = $1 AND a.account_type = 'REVENUE'
            ORDER BY jel.credit_amount DESC LIMIT 1
            """,
            entry_id,
        )
    else:
        cat_row = await conn.fetchrow(
            """
            SELECT a.name FROM journal_entry_lines jel
            JOIN accounts a ON a.id = jel.account_id
            WHERE jel.journal_entry_id = $1 AND a.account_type = 'EXPENSE'
            ORDER BY jel.debit_amount DESC LIMIT 1
            """,
            entry_id,
        )

    category = cat_row["name"] if cat_row else "Uncategorised"
    vendor   = row["vendor_name"] or "Unknown vendor"
    amount   = row["gross_amount"] or Decimal(0)
    date_str = _fmt_date(row["document_date"])

    return f"{vendor} — R {amount:,.2f}\nCategory: {category}\nDate: {date_str}"


# ---------------------------------------------------------------------------
# Reversing entry
# ---------------------------------------------------------------------------

async def _reverse_entry(
    conn: asyncpg.Connection,
    user_id: UUID,
    entry_id: UUID,
) -> None:
    """
    Create and post a reversing journal entry for a POSTED entry.
    For a DRAFT entry, simply deletes it (no accounting impact yet).
    """
    status = await conn.fetchval(
        "SELECT status FROM journal_entries WHERE id = $1", entry_id
    )

    if status == "DRAFT":
        await conn.execute("DELETE FROM vat_entries WHERE journal_entry_id = $1", entry_id)
        await conn.execute("DELETE FROM journal_entries WHERE id = $1", entry_id)
        return

    # POSTED — create equal-and-opposite reversing entry
    original = await conn.fetchrow(
        "SELECT document_id, entry_date, description FROM journal_entries WHERE id = $1",
        entry_id,
    )
    lines = await conn.fetch(
        """
        SELECT account_id, debit_amount, credit_amount, description, line_order
        FROM   journal_entry_lines
        WHERE  journal_entry_id = $1
        ORDER  BY line_order
        """,
        entry_id,
    )

    rev_id: UUID = await conn.fetchval(
        """
        INSERT INTO journal_entries
            (user_id, document_id, entry_date, description, status, is_ai_generated, ai_confidence)
        VALUES ($1, $2, CURRENT_DATE, $3, 'DRAFT', FALSE, 1.0)
        RETURNING id
        """,
        user_id,
        original["document_id"],
        f"REVERSAL: {original['description']}",
    )

    for line in lines:
        await conn.execute(
            """
            INSERT INTO journal_entry_lines
                (journal_entry_id, account_id, debit_amount, credit_amount, description, line_order)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            rev_id,
            line["account_id"],
            line["credit_amount"],    # swap debit ↔ credit
            line["debit_amount"],
            f"Reversal: {line['description'] or ''}",
            line["line_order"],
        )

    await conn.execute(
        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", rev_id
    )
    log.info("Reversing entry %s created for original %s", rev_id, entry_id)


# ---------------------------------------------------------------------------
# Re-categorise helpers
# ---------------------------------------------------------------------------

async def _recategorise_draft(
    conn: asyncpg.Connection,
    user_id: UUID,
    entry_id: UUID,
    hint: str,
) -> UUID:
    """
    Reverse/delete the existing entry and re-run categorisation with the user's hint.
    Returns the new journal entry UUID.
    """
    import json as _json

    row = await conn.fetchrow(
        "SELECT document_id FROM journal_entries WHERE id = $1", entry_id
    )
    document_id = row["document_id"]

    ocr_row = await conn.fetchrow(
        "SELECT ocr_raw_json FROM documents WHERE id = $1", document_id
    )
    raw_json = ocr_row["ocr_raw_json"]
    if isinstance(raw_json, str):
        raw_json = _json.loads(raw_json)

    await _reverse_entry(conn, user_id, entry_id)

    expense_raw = textract_parser.parse(raw_json)
    valid_codes = {r["code"] for r in await conn.fetch(
        "SELECT code FROM accounts WHERE user_id = $1 AND is_active = TRUE",
        user_id,
    )}
    expense_categorised = llm_categoriser.categorise(
        expense_raw, valid_codes, hint=hint
    )
    expense_categorised = expense_categorised.with_grossed_up_line_items()

    return await journal_writer.write(conn, user_id, document_id, expense_categorised)


async def _correct_amount(
    conn: asyncpg.Connection,
    user_id: UUID,
    entry_id: UUID,
    correct_gross: Decimal,
) -> UUID:
    """
    Reverse the original entry and re-record it with a corrected gross total.
    Line item amounts are scaled proportionally; VAT is re-derived.
    """
    import json as _json
    from app.models.extraction import VatCode

    row = await conn.fetchrow(
        """
        SELECT je.document_id, d.gross_amount AS original_gross,
               d.document_type::text AS doc_type
        FROM   journal_entries je
        JOIN   documents       d ON d.id = je.document_id
        WHERE  je.id = $1
        """,
        entry_id,
    )
    document_id    = row["document_id"]
    original_gross = Decimal(str(row["original_gross"])) if row["original_gross"] else Decimal(1)
    is_sale        = (row["doc_type"] or "PURCHASE") == "SALE"

    ocr_row = await conn.fetchrow(
        "SELECT ocr_raw_json FROM documents WHERE id = $1", document_id
    )
    raw_json = ocr_row["ocr_raw_json"]
    if isinstance(raw_json, str):
        raw_json = _json.loads(raw_json)

    await _reverse_entry(conn, user_id, entry_id)

    # Re-parse and scale the gross total to the corrected figure
    expense_raw = textract_parser.parse(raw_json)
    scale        = correct_gross / original_gross if original_gross else Decimal(1)
    scaled_items = []
    from app.models.extraction import CategorisedLineItem

    # Get the original line items with their account codes from the (now reversed) entry
    # Re-categorise is simpler — just hint with the new amount
    valid_codes = {r["code"] for r in await conn.fetch(
        "SELECT code FROM accounts WHERE user_id = $1 AND is_active = TRUE",
        user_id,
    )}
    hint = f"correct gross total is R{correct_gross:,.2f}"
    expense_categorised = llm_categoriser.categorise(
        expense_raw, valid_codes, hint=hint
    )

    # Override gross total to the user-provided correct figure
    from app.models.extraction import CategorisedExpense
    expense_corrected = expense_categorised.model_copy(update={
        "gross_total": correct_gross,
        "line_items": [
            item.model_copy(update={
                "gross_amount": (item.gross_amount * scale).quantize(Decimal("0.01"))
            })
            for item in expense_categorised.line_items
        ],
    })
    expense_corrected = expense_corrected.with_grossed_up_line_items()

    if is_sale:
        return await journal_writer.write_sale(conn, user_id, document_id, expense_corrected)
    return await journal_writer.write(conn, user_id, document_id, expense_corrected)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

async def _set_conversation_state(
    user_id: UUID,
    state: str | None,
    pending_entry_id: UUID | None = ...,   # type: ignore[assignment]
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if pending_entry_id is ...:
            # Don't touch pending_entry_id
            await conn.execute(
                "UPDATE users SET conversation_state = $2::conversation_state, updated_at = NOW() WHERE id = $1",
                user_id,
                state,
            )
        else:
            await conn.execute(
                """
                UPDATE users SET
                    conversation_state = $2::conversation_state,
                    pending_entry_id   = $3,
                    updated_at         = NOW()
                WHERE id = $1
                """,
                user_id,
                state,
                pending_entry_id,
            )


async def _cmd_balance(conn: asyncpg.Connection, user_id: UUID) -> str:
    """Income vs expenses for the current calendar month."""
    from datetime import date as _date
    today = _date.today()
    rows = await conn.fetch(
        """
        SELECT a.account_type::text AS account_type, COALESCE(SUM(vab.balance), 0) AS total
        FROM   accounts a
        LEFT JOIN v_account_balances vab
            ON  vab.account_id = a.id
            AND vab.period_year  = $2
            AND vab.period_month = $3
        WHERE  a.user_id     = $1
          AND  a.account_type IN ('REVENUE', 'EXPENSE')
        GROUP  BY a.account_type
        """,
        user_id, today.year, today.month,
    )
    totals = {r["account_type"]: Decimal(str(r["total"])) for r in rows}
    income   = totals.get("REVENUE", Decimal(0))
    expenses = totals.get("EXPENSE", Decimal(0))
    net      = income - expenses
    month    = today.strftime("%B %Y")
    sign     = "+" if net >= 0 else ""
    return (
        f"📊 *{month} snapshot*\n\n"
        f"Income:    R {income:>10,.2f}\n"
        f"Expenses:  R {expenses:>10,.2f}\n"
        f"{'─' * 26}\n"
        f"Net:      {sign}R {abs(net):>9,.2f}"
    )


async def _cmd_last(conn: asyncpg.Connection, user_id: UUID) -> str:
    """Details of the most recent journal entry."""
    row = await conn.fetchrow(
        """
        SELECT je.id, je.status, d.vendor_name, d.gross_amount,
               d.document_date, d.document_type::text AS document_type
        FROM   journal_entries je
        JOIN   documents       d ON d.id = je.document_id
        WHERE  je.user_id = $1
        ORDER  BY je.created_at DESC
        LIMIT  1
        """,
        user_id,
    )
    if row is None:
        return "No entries recorded yet. Send me a receipt to get started."

    is_sale  = (row["document_type"] or "PURCHASE") == "SALE"
    cat_row  = await conn.fetchrow(
        f"""
        SELECT a.name FROM journal_entry_lines jel
        JOIN accounts a ON a.id = jel.account_id
        WHERE jel.journal_entry_id = $1 AND a.account_type = '{"REVENUE" if is_sale else "EXPENSE"}'
        ORDER BY {'jel.credit_amount' if is_sale else 'jel.debit_amount'} DESC LIMIT 1
        """,
        row["id"],
    )
    category  = cat_row["name"] if cat_row else "Uncategorised"
    status    = "✅ Posted" if row["status"] == "POSTED" else "⏳ Pending confirmation"
    type_icon = "💰" if is_sale else "🧾"
    return (
        f"📋 *Last entry*\n\n"
        f"{type_icon} *{row['vendor_name'] or 'Unknown vendor'}* — R {row['gross_amount']:,.2f}\n"
        f"Category: {category}\n"
        f"Date: {_fmt_date(row['document_date'])}\n"
        f"Status: {status}\n\n"
        f"Reply *EDIT* if anything looks wrong."
    )


async def _cmd_top(conn: asyncpg.Connection, user_id: UUID) -> str:
    """Top 5 expense categories this calendar month."""
    from datetime import date as _date
    today = _date.today()
    rows = await conn.fetch(
        """
        SELECT a.name, COALESCE(SUM(vab.balance), 0) AS total
        FROM   accounts a
        LEFT JOIN v_account_balances vab
            ON  vab.account_id = a.id
            AND vab.period_year  = $2
            AND vab.period_month = $3
        WHERE  a.user_id     = $1
          AND  a.account_type = 'EXPENSE'
        GROUP  BY a.name
        HAVING COALESCE(SUM(vab.balance), 0) > 0
        ORDER  BY total DESC
        LIMIT  5
        """,
        user_id, today.year, today.month,
    )
    if not rows:
        return f"No expenses recorded for {today.strftime('%B %Y')} yet."
    month = today.strftime("%B %Y")
    lines = "\n".join(
        f"  {i+1}. {r['name']} — R {Decimal(str(r['total'])):,.2f}"
        for i, r in enumerate(rows)
    )
    return f"🏆 *Top categories — {month}*\n\n{lines}"


async def _cmd_pending(conn: asyncpg.Connection, user_id: UUID) -> str:
    """List all DRAFT journal entries awaiting confirmation."""
    rows = await conn.fetch(
        """
        SELECT je.id, d.vendor_name, d.gross_amount, d.document_date
        FROM   journal_entries je
        JOIN   documents       d ON d.id = je.document_id
        WHERE  je.user_id = $1 AND je.status = 'DRAFT'
        ORDER  BY je.created_at DESC
        LIMIT  10
        """,
        user_id,
    )
    if not rows:
        return "⏳ No receipts awaiting review. You're all up to date!"
    lines = "\n".join(
        f"  • {r['vendor_name'] or 'Unknown'} — R {r['gross_amount']:,.2f} "
        f"({_fmt_date(r['document_date'])})"
        for r in rows
    )
    plural = "receipt" if len(rows) == 1 else "receipts"
    return (
        f"⏳ *{len(rows)} {plural} awaiting review*\n\n{lines}\n\n"
        f"Reply *YES* to confirm the most recent one, or *NO* to flag an issue."
    )


async def _enter_edit_flow(
    conn: asyncpg.Connection,
    user_id: UUID,
    entry_id: UUID,
    from_number: str,
) -> None:
    """Store pending_entry_id, set AWAITING_EDIT_CHOICE, send the edit menu."""
    summary = await _entry_summary(conn, entry_id)
    await conn.execute(
        """
        UPDATE users SET
            conversation_state = 'AWAITING_EDIT_CHOICE'::conversation_state,
            pending_entry_id   = $2,
            updated_at         = NOW()
        WHERE id = $1
        """,
        user_id,
        entry_id,
    )
    twilio_client.send_whatsapp(
        from_number,
        _MSG_EDIT_MENU.format(summary=summary),
    )


async def _deliver_report(
    from_number: str,
    user_id: UUID,
    fy_end_month: int,
    fy_year: int,
) -> None:
    """Generate and deliver a PDF report for a specific financial year."""
    try:
        twilio_client.send_whatsapp(
            from_number,
            f"📊 Generating your {fy_year} financial report... this may take a moment."
        )
        result = await report_orchestrator.generate_and_deliver(user_id, fy_end_month, fy_year)
        if result is None:
            msg = (
                f"No posted transactions found for the {fy_year} financial year.\n\n"
                f"Upload receipts and confirm them to see your financial statements."
            )
        else:
            msg = (
                f"Your {fy_year} financial report is ready! 📊\n\n"
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


async def _find_last_posted_entry(
    conn: asyncpg.Connection, user_id: UUID
) -> UUID | None:
    """Return the UUID of the user's most recently posted journal entry, or None."""
    return await conn.fetchval(
        """
        SELECT id FROM journal_entries
        WHERE  user_id = $1 AND status = 'POSTED'
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


async def _build_result_message(
    conn: asyncpg.Connection,
    entry_id: UUID,
) -> str:
    """
    Build the post-pipeline notification showing a full line-by-line breakdown.
    Always ends with 'Is this correct? YES / NO' — entry is left as DRAFT until
    the user confirms.
    """
    row = await conn.fetchrow(
        """
        SELECT d.vendor_name, d.gross_amount,
               d.document_type::text AS document_type, d.document_date
        FROM   journal_entries je
        JOIN   documents       d ON d.id = je.document_id
        WHERE  je.id = $1
        """,
        entry_id,
    )

    is_sale = (row["document_type"] or "PURCHASE") == "SALE"
    vendor   = row["vendor_name"] or "Unknown vendor"
    gross    = Decimal(str(row["gross_amount"] or 0))
    date_str = _fmt_date(row["document_date"])

    lines = await conn.fetch(
        """
        SELECT jel.debit_amount, jel.credit_amount,
               a.name, a.account_type::text AS account_type, a.code
        FROM   journal_entry_lines jel
        JOIN   accounts a ON a.id = jel.account_id
        WHERE  jel.journal_entry_id = $1
        ORDER  BY jel.line_order
        """,
        entry_id,
    )

    is_credit_sale = any(line["code"] == "1110" for line in lines)

    breakdown = []
    for line in lines:
        code         = line["code"]
        name         = line["name"]
        account_type = line["account_type"]

        if code in ("1020", "1110"):
            continue

        if account_type == "EXPENSE":
            amt = Decimal(str(line["debit_amount"]))
            if amt > 0:
                breakdown.append(f"  {name} — R {amt:,.2f}")
        elif code == "1200":
            amt = Decimal(str(line["debit_amount"]))
            if amt > 0:
                breakdown.append(f"  VAT (15%) — R {amt:,.2f}")
        elif account_type == "REVENUE":
            amt = Decimal(str(line["credit_amount"]))
            if amt > 0:
                breakdown.append(f"  {name} — R {amt:,.2f}")
        elif code == "2100":
            amt = Decimal(str(line["credit_amount"]))
            if amt > 0:
                breakdown.append(f"  VAT Output (15%) — R {amt:,.2f}")

    breakdown_text = "\n".join(breakdown) if breakdown else "  (no line items)"
    divider = "  " + "─" * 30

    if is_sale:
        icon = "💰"
        if is_credit_sale:
            header     = f"{icon} *{vendor}* — {date_str}\n  _(outstanding — not yet paid)_"
            total_line = f"  Amount owed: R {gross:,.2f}"
        else:
            header     = f"{icon} *{vendor}* — {date_str}"
            total_line = f"  Total received: R {gross:,.2f}"
    else:
        icon       = "🧾"
        header     = f"{icon} *{vendor}* — {date_str}"
        total_line = f"  Total: R {gross:,.2f}"

    return (
        f"📋 Here's what I recorded:\n\n"
        f"{header}\n"
        f"{breakdown_text}\n"
        f"{divider}\n"
        f"{total_line}\n\n"
        f"Is this correct?\n"
        f"Reply *YES* to save it or *NO* if something's wrong."
    )


# ---------------------------------------------------------------------------
# Media handler
# ---------------------------------------------------------------------------

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

    twilio_client.send_whatsapp(from_number, _MSG_PROCESSING)

    try:
        content, mime_type = await media_handler.download_media(media_url, media_content_type)
    except ValueError as exc:
        twilio_client.send_whatsapp(from_number, _MSG_UNSUPPORTED_MEDIA)
        log.warning("Unsupported media from %s: %s", from_number, exc)
        return

    if receipt_checker.contains_multiple_receipts(content, mime_type):
        twilio_client.send_whatsapp(
            from_number,
            "📸 It looks like your photo contains more than one receipt.\n\n"
            "Please send one receipt at a time so I can record each one accurately."
        )
        log.info("Rejected multi-receipt image from %s", from_number)
        return

    document_id: UUID = await conn.fetchval(
        """
        INSERT INTO documents
            (user_id, s3_bucket, s3_key, mime_type, file_size_bytes,
             whatsapp_message_id, status)
        VALUES ($1, '', '', $2, $3, $4, 'PENDING')
        RETURNING id
        """,
        user_id, mime_type, len(content), whatsapp_msg_id,
    )

    bucket, key, etag = media_handler.upload_to_s3(content, mime_type, user_id, document_id)
    await conn.execute(
        "UPDATE documents SET s3_bucket=$2, s3_key=$3, s3_etag=$4, updated_at=NOW() WHERE id=$1",
        document_id, bucket, key, etag,
    )

    raw_textract = media_handler.analyze_expense(bucket, key)

    from app.pipeline import process_document
    # Always leave as DRAFT from the WhatsApp flow — user confirms with YES/NO
    entry_id = await process_document(document_id, raw_textract, auto_post=False)

    if entry_id is None:
        # Pipeline paused — check why by looking at what was stored on the document
        doc_row = await conn.fetchrow(
            "SELECT document_type, vendor_name, gross_amount FROM documents WHERE id = $1",
            document_id,
        )
        stored_type = doc_row["document_type"] if doc_row else None

        if stored_type == "SALE":
            # We know it's a sale — ask whether payment has been received
            vendor = doc_row["vendor_name"] or "Unknown"
            amount = Decimal(str(doc_row["gross_amount"] or 0))
            await conn.execute(
                """
                UPDATE users SET
                    conversation_state  = 'AWAITING_PAYMENT_CONFIRMED'::conversation_state,
                    pending_document_id = $2,
                    updated_at          = NOW()
                WHERE id = $1
                """,
                user_id, document_id,
            )
            twilio_client.send_whatsapp(
                from_number,
                f"I can see a sales invoice from *{vendor}* for *R {amount:,.2f}*.\n\n"
                f"Has this payment been received into your account?\n\n"
                f"*YES* — money already received\n"
                f"*NO* — still waiting for payment"
            )
        else:
            # Document type is uncertain — ask expense or income
            await conn.execute(
                """
                UPDATE users SET
                    conversation_state  = 'AWAITING_DOCUMENT_TYPE'::conversation_state,
                    pending_document_id = $2,
                    updated_at          = NOW()
                WHERE id = $1
                """,
                user_id, document_id,
            )
            twilio_client.send_whatsapp(from_number, _MSG_ASK_DOCUMENT_TYPE)
        return

    msg = await _build_result_message(conn, entry_id)
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
    from_raw    = form_data.get("From", "")
    from_number = _normalise_number(from_raw)
    body        = form_data.get("Body", "").strip()
    num_media   = int(form_data.get("NumMedia", 0))
    latitude    = form_data.get("Latitude")
    longitude   = form_data.get("Longitude")

    if not from_number:
        log.error("Received message with no From field: %s", form_data)
        return

    if _is_rate_limited(from_number):
        log.warning("Rate limit hit for %s — dropping message", from_number)
        twilio_client.send_whatsapp(
            from_number,
            "You're sending messages too quickly. Please wait a moment and try again."
        )
        return

    pool = None
    conn = None
    try:
        pool = await get_pool()
        conn = await pool.acquire()
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
            twilio_client.send_whatsapp(from_number, _MSG_ASK_PROVINCE)
            return

        if onboarding_step == "PROVINCE":
            choice = body.strip()
            province = _PROVINCE_MAP.get(choice)
            if province is None:
                twilio_client.send_whatsapp(
                    from_number,
                    "Please reply with a number from 1 to 9.\n\n" + _MSG_ASK_PROVINCE
                )
                return
            await conn.execute(
                "UPDATE users SET province = $2, onboarding_step = 'BUSINESS_TYPE', updated_at = NOW() WHERE id = $1",
                user_id, province,
            )
            twilio_client.send_whatsapp(from_number, _MSG_ASK_BUSINESS_TYPE)
            return

        if onboarding_step == "BUSINESS_TYPE":
            choice = body.strip()
            business_type = _BUSINESS_TYPE_MAP.get(choice)
            if business_type is None:
                twilio_client.send_whatsapp(
                    from_number,
                    "Please reply with a number from 1 to 10.\n\n" + _MSG_ASK_BUSINESS_TYPE
                )
                return
            await conn.execute(
                "UPDATE users SET business_type = $2, onboarding_step = 'TAX_REF', updated_at = NOW() WHERE id = $1",
                user_id, business_type,
            )
            twilio_client.send_whatsapp(from_number, _MSG_ASK_TAX_REF)
            return

        if onboarding_step == "TAX_REF":
            tax_ref = None if body.strip().upper() == "SKIP" else body.strip()
            await _save_tax_ref(conn, user_id, tax_ref)
            twilio_client.send_whatsapp(from_number, _MSG_ONBOARDING_DONE)
            return

        # ------------------------------------------------------------------
        # Opportunistic location capture
        # If the user voluntarily shares their WhatsApp location at any point,
        # store it — no response needed, just save quietly.
        # ------------------------------------------------------------------
        if latitude and longitude:
            try:
                await conn.execute(
                    "UPDATE users SET latitude = $2, longitude = $3, updated_at = NOW() WHERE id = $1",
                    user_id, float(latitude), float(longitude),
                )
                log.info("Location updated for user %s: %s, %s", user_id, latitude, longitude)
            except Exception:
                log.warning("Failed to save location for user %s", user_id)
            if not body and num_media == 0:
                return  # pure location share — nothing else to process

        # ------------------------------------------------------------------
        # Fully onboarded — media upload
        # ------------------------------------------------------------------
        if num_media > 0:
            try:
                await _handle_media_message(conn, user_id, from_number, form_data)
            except Exception:
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

        # ── AWAITING_DOCUMENT_TYPE ─────────────────────────────────────────
        if conversation_state == "AWAITING_DOCUMENT_TYPE":
            from app.pipeline import resume_document_with_type
            from app.models.extraction import DocumentType

            normalised = body.strip().upper()
            if normalised in ("EXPENSE", "PURCHASE", "BILL", "RECEIPT"):
                doc_type = DocumentType.PURCHASE
            elif normalised in ("INCOME", "SALE", "INVOICE", "REVENUE"):
                doc_type = DocumentType.SALE
            else:
                twilio_client.send_whatsapp(
                    from_number,
                    "Please reply *EXPENSE* (for a purchase/receipt you paid) "
                    "or *INCOME* (for a sales invoice you issued)."
                )
                return

            pending_doc_id = await conn.fetchval(
                "SELECT pending_document_id FROM users WHERE id = $1", user_id
            )
            if pending_doc_id is None:
                await _set_conversation_state(user_id, None)
                twilio_client.send_whatsapp(
                    from_number,
                    "No document is waiting for clarification. Send a receipt to get started."
                )
                return

            await conn.execute(
                """
                UPDATE users SET
                    conversation_state  = NULL,
                    pending_document_id = NULL,
                    updated_at          = NOW()
                WHERE id = $1
                """,
                user_id,
            )

            try:
                entry_id = await resume_document_with_type(pending_doc_id, doc_type, auto_post=False)
            except Exception:
                log.exception("Resume failed for document %s (user %s)", pending_doc_id, user_id)
                twilio_client.send_whatsapp(
                    from_number,
                    "Sorry, something went wrong processing your document. "
                    "Please try uploading it again."
                )
                return

            pool_inner = await get_pool()
            async with pool_inner.acquire() as _conn:
                msg = await _build_result_message(_conn, entry_id)
            twilio_client.send_whatsapp(from_number, msg)
            return

        # ── AWAITING_CASH_SALE_DESCRIPTION ───────────────────────────────
        if conversation_state == "AWAITING_CASH_SALE_DESCRIPTION":
            if not body.strip():
                twilio_client.send_whatsapp(from_number, _MSG_ASK_CASH_SALE_DESCRIPTION)
                return
            await conn.execute(
                """
                UPDATE users SET
                    conversation_state       = 'AWAITING_CASH_SALE_AMOUNT'::conversation_state,
                    pending_sale_description = $2,
                    updated_at               = NOW()
                WHERE id = $1
                """,
                user_id, body.strip(),
            )
            twilio_client.send_whatsapp(from_number, _MSG_ASK_CASH_SALE_AMOUNT)
            return

        # ── AWAITING_CASH_SALE_AMOUNT ─────────────────────────────────────
        if conversation_state == "AWAITING_CASH_SALE_AMOUNT":
            gross = _parse_zar_amount(body)
            if gross is None:
                twilio_client.send_whatsapp(
                    from_number,
                    "I couldn't read that amount. Please reply with just the number:\n"
                    "E.g. *1500* or *R 1 500.00*"
                )
                return

            sale_description = await conn.fetchval(
                "SELECT pending_sale_description FROM users WHERE id = $1", user_id
            )
            if not sale_description:
                await conn.execute(
                    "UPDATE users SET conversation_state = NULL, updated_at = NOW() WHERE id = $1",
                    user_id,
                )
                twilio_client.send_whatsapp(
                    from_number,
                    "Something went wrong — I lost the description. Please type *CASH SALE* to start again."
                )
                return

            await conn.execute(
                """
                UPDATE users SET
                    conversation_state       = NULL,
                    pending_sale_description = NULL,
                    updated_at               = NOW()
                WHERE id = $1
                """,
                user_id,
            )

            try:
                from app.services.categorisation.revenue_categoriser import categorise_text_sale
                from app.services.ledger import journal_writer
                from datetime import date as _date
                import anthropic as _anthropic

                valid_codes = {r["code"] for r in await conn.fetch(
                    "SELECT code FROM accounts WHERE user_id = $1 AND is_active = TRUE", user_id
                )}
                account_code, vat_code, confidence = categorise_text_sale(
                    description=sale_description,
                    gross_total=gross,
                    valid_account_codes=valid_codes,
                    anthropic_client=_anthropic.Anthropic(),
                )
                entry_id = await journal_writer.write_cash_sale(
                    conn=conn,
                    user_id=user_id,
                    description=sale_description,
                    gross_total=gross,
                    account_code=account_code,
                    vat_code=vat_code,
                    entry_date=_date.today(),
                    confidence=confidence,
                )

                from decimal import Decimal as _D
                from app.models.extraction import VatCode as _VatCode
                vat_rate   = _D("0.15") if vat_code == _VatCode.SR else _D("0.00")
                net_amount = (gross / (1 + vat_rate)).quantize(_D("0.01"))
                vat_amount = (gross - net_amount).quantize(_D("0.01"))

                # Look up the revenue account name for the breakdown
                acct_name = await conn.fetchval(
                    "SELECT name FROM accounts WHERE user_id = $1 AND code = $2",
                    user_id, account_code,
                ) or "Sales"

                divider = "  " + "─" * 30
                vat_line = f"  VAT Output (15%) — R {vat_amount:,.2f}\n" if vat_amount > 0 else ""
                msg = (
                    f"📋 Here's what I recorded:\n\n"
                    f"💰 *Cash sale* — {_date.today().strftime('%d %b %Y')}\n"
                    f"  {acct_name} — R {net_amount:,.2f}\n"
                    f"{vat_line}"
                    f"{divider}\n"
                    f"  Total received: R {gross:,.2f}\n\n"
                    f"Is this correct?\n"
                    f"Reply *YES* to save it or *NO* if something's wrong."
                )
                twilio_client.send_whatsapp(from_number, msg)
            except Exception:
                log.exception("Cash sale write failed for user %s", user_id)
                twilio_client.send_whatsapp(
                    from_number,
                    "Sorry, something went wrong recording that sale. Please try again."
                )
            return

        # ── AWAITING_PAYMENT_CONFIRMED ────────────────────────────────────
        if conversation_state == "AWAITING_PAYMENT_CONFIRMED":
            from app.pipeline import complete_sale_with_payment_status

            normalised = body.strip().upper()
            if normalised in ("YES", "Y", "JA"):
                is_paid = True
            elif normalised in ("NO", "N", "NEE"):
                is_paid = False
            else:
                twilio_client.send_whatsapp(
                    from_number,
                    "Please reply *YES* (payment already received) or *NO* (still waiting for payment)."
                )
                return

            pending_doc_id = await conn.fetchval(
                "SELECT pending_document_id FROM users WHERE id = $1", user_id
            )
            if pending_doc_id is None:
                await _set_conversation_state(user_id, None)
                twilio_client.send_whatsapp(
                    from_number,
                    "No document is waiting. Send a receipt to get started."
                )
                return

            await conn.execute(
                """
                UPDATE users SET
                    conversation_state  = NULL,
                    pending_document_id = NULL,
                    updated_at          = NOW()
                WHERE id = $1
                """,
                user_id,
            )

            try:
                entry_id = await complete_sale_with_payment_status(pending_doc_id, is_paid, auto_post=False)
                pool_inner = await get_pool()
                async with pool_inner.acquire() as _conn:
                    msg = await _build_result_message(_conn, entry_id)
                twilio_client.send_whatsapp(from_number, msg)
            except Exception:
                log.exception("Failed to complete sale for document %s", pending_doc_id)
                twilio_client.send_whatsapp(
                    from_number,
                    "Sorry, something went wrong processing that document. Please try uploading it again."
                )
            return

        # ── AWAITING_CORRECT_COUNTERPARTY ─────────────────────────────────
        if conversation_state == "AWAITING_CORRECT_COUNTERPARTY":
            if not body.strip():
                twilio_client.send_whatsapp(from_number, _MSG_ASK_CORRECT_COUNTERPARTY)
                return

            pending_entry_id = await conn.fetchval(
                "SELECT pending_entry_id FROM users WHERE id = $1", user_id
            )
            if pending_entry_id is None:
                await _set_conversation_state(user_id, None)
                twilio_client.send_whatsapp(from_number, _MSG_NO_PENDING)
                return

            correct_name = body.strip()
            try:
                # Update vendor_name on the document and description on the entry
                await conn.execute(
                    """
                    UPDATE documents SET vendor_name = $2, updated_at = NOW()
                    WHERE id = (SELECT document_id FROM journal_entries WHERE id = $1)
                    """,
                    pending_entry_id, correct_name,
                )
                await conn.execute(
                    """
                    UPDATE journal_entries
                    SET description = REGEXP_REPLACE(description, '^(.*?:).*', '\\1 ' || $2),
                        updated_at  = NOW()
                    WHERE id = $1
                    """,
                    pending_entry_id, correct_name,
                )
                await conn.execute(
                    """
                    UPDATE users SET
                        conversation_state = NULL,
                        pending_entry_id   = NULL,
                        updated_at         = NOW()
                    WHERE id = $1
                    """,
                    user_id,
                )
                twilio_client.send_whatsapp(
                    from_number,
                    f"Done — updated to *{correct_name}*. ✅\n\nIf anything else looks wrong, reply *EDIT*."
                )
            except Exception:
                log.exception("Counterparty correction failed for entry %s", pending_entry_id)
                await conn.execute(
                    "UPDATE users SET conversation_state = NULL, pending_entry_id = NULL, updated_at = NOW() WHERE id = $1",
                    user_id,
                )
                twilio_client.send_whatsapp(from_number, "Sorry, I couldn't update the name. Please try again.")
            return

        # ── AWAITING_EDIT_CHOICE ───────────────────────────────────────────
        if conversation_state == "AWAITING_EDIT_CHOICE":
            pending_entry_id = await conn.fetchval(
                "SELECT pending_entry_id FROM users WHERE id = $1", user_id
            )
            if pending_entry_id is None:
                await _set_conversation_state(user_id, None, pending_entry_id=None)
                twilio_client.send_whatsapp(from_number, _MSG_NO_PENDING)
                return

            choice = body.strip()

            if choice == "1":
                # Wrong type — reverse and re-ask EXPENSE vs INCOME
                try:
                    doc_id = await conn.fetchval(
                        "SELECT document_id FROM journal_entries WHERE id = $1", pending_entry_id
                    )
                    await _reverse_entry(conn, user_id, pending_entry_id)
                    await conn.execute(
                        """
                        UPDATE users SET
                            conversation_state  = 'AWAITING_DOCUMENT_TYPE'::conversation_state,
                            pending_entry_id    = NULL,
                            pending_document_id = $2,
                            updated_at          = NOW()
                        WHERE id = $1
                        """,
                        user_id, doc_id,
                    )
                    twilio_client.send_whatsapp(from_number, _MSG_ASK_DOCUMENT_TYPE)
                except Exception:
                    log.exception("Type correction failed for entry %s", pending_entry_id)
                    await conn.execute(
                        "UPDATE users SET conversation_state = NULL, pending_entry_id = NULL, updated_at = NOW() WHERE id = $1",
                        user_id,
                    )
                    twilio_client.send_whatsapp(from_number, "Sorry, I couldn't fix that. Please try again.")

            elif choice == "2":
                await conn.execute(
                    "UPDATE users SET conversation_state = 'AWAITING_CORRECT_AMOUNT'::conversation_state, updated_at = NOW() WHERE id = $1",
                    user_id,
                )
                twilio_client.send_whatsapp(from_number, _MSG_ASK_CORRECT_AMOUNT)

            elif choice == "3":
                await conn.execute(
                    "UPDATE users SET conversation_state = 'AWAITING_CORRECT_COUNTERPARTY'::conversation_state, updated_at = NOW() WHERE id = $1",
                    user_id,
                )
                twilio_client.send_whatsapp(from_number, _MSG_ASK_CORRECT_COUNTERPARTY)

            elif choice == "4":
                await conn.execute(
                    "UPDATE users SET conversation_state = 'AWAITING_CATEGORY_HINT'::conversation_state, updated_at = NOW() WHERE id = $1",
                    user_id,
                )
                twilio_client.send_whatsapp(from_number, _MSG_ASK_CATEGORY_HINT)

            elif choice == "5":
                try:
                    await _reverse_entry(conn, user_id, pending_entry_id)
                    await conn.execute(
                        "UPDATE users SET conversation_state = NULL, pending_entry_id = NULL, updated_at = NOW() WHERE id = $1",
                        user_id,
                    )
                    twilio_client.send_whatsapp(
                        from_number,
                        "Done — that entry has been removed from your books. ✅"
                    )
                except Exception:
                    log.exception("Reversal failed for entry %s", pending_entry_id)
                    await conn.execute(
                        "UPDATE users SET conversation_state = NULL, pending_entry_id = NULL, updated_at = NOW() WHERE id = $1",
                        user_id,
                    )
                    twilio_client.send_whatsapp(from_number, "Sorry, I couldn't remove that entry. Please try again.")

            else:
                twilio_client.send_whatsapp(
                    from_number,
                    "Please reply with *1*, *2*, *3*, *4*, or *5*."
                )
            return

        # ── AWAITING_CORRECT_AMOUNT ────────────────────────────────────────
        if conversation_state == "AWAITING_CORRECT_AMOUNT":
            pending_entry_id = await conn.fetchval(
                "SELECT pending_entry_id FROM users WHERE id = $1", user_id
            )
            if pending_entry_id is None:
                await conn.execute(
                    "UPDATE users SET conversation_state = NULL, updated_at = NOW() WHERE id = $1",
                    user_id,
                )
                twilio_client.send_whatsapp(from_number, _MSG_NO_PENDING)
                return

            correct_amount = _parse_zar_amount(body)
            if correct_amount is None:
                twilio_client.send_whatsapp(
                    from_number,
                    "I couldn't read that amount. Please reply with just the number:\n"
                    "E.g. *8900* or *R 8 900.00*"
                )
                return

            try:
                new_entry_id = await _correct_amount(conn, user_id, pending_entry_id, correct_amount)
                await conn.execute(
                    "UPDATE users SET conversation_state = NULL, pending_entry_id = NULL, updated_at = NOW() WHERE id = $1",
                    user_id,
                )
                # Fetch summary of original for the confirmation message
                orig_summary = await _entry_summary(conn, pending_entry_id) if False else ""
                twilio_client.send_whatsapp(
                    from_number,
                    f"Done. I've posted a reversing entry and recorded R {correct_amount:,.2f} instead. ✅"
                )
            except Exception:
                log.exception("Amount correction failed for entry %s", pending_entry_id)
                await conn.execute(
                    "UPDATE users SET conversation_state = NULL, pending_entry_id = NULL, updated_at = NOW() WHERE id = $1",
                    user_id,
                )
                twilio_client.send_whatsapp(
                    from_number, "Sorry, I couldn't apply that correction. Please try again."
                )
            return

        # ── AWAITING_CATEGORY_HINT ─────────────────────────────────────────
        if conversation_state == "AWAITING_CATEGORY_HINT":
            if not body:
                twilio_client.send_whatsapp(from_number, _MSG_ASK_CATEGORY_HINT)
                return

            pending_entry_id = await conn.fetchval(
                "SELECT pending_entry_id FROM users WHERE id = $1", user_id
            )
            if pending_entry_id is None:
                await conn.execute(
                    "UPDATE users SET conversation_state = NULL, updated_at = NOW() WHERE id = $1",
                    user_id,
                )
                twilio_client.send_whatsapp(from_number, _MSG_NO_PENDING)
                return

            try:
                new_entry_id = await _recategorise_draft(conn, user_id, pending_entry_id, hint=body)
                await conn.execute(
                    "UPDATE users SET conversation_state = NULL, pending_entry_id = NULL, updated_at = NOW() WHERE id = $1",
                    user_id,
                )
                msg = await _build_result_message(conn, new_entry_id)
                msg = "Re-categorised and posted:\n\n" + msg.replace(
                    "If anything looks wrong, reply *EDIT* within 24 hours.",
                    "If anything still looks wrong, reply *EDIT*."
                )
                twilio_client.send_whatsapp(from_number, msg)
            except Exception:
                log.exception("Re-categorisation failed for entry %s", pending_entry_id)
                await conn.execute(
                    "UPDATE users SET conversation_state = NULL, pending_entry_id = NULL, updated_at = NOW() WHERE id = $1",
                    user_id,
                )
                twilio_client.send_whatsapp(
                    from_number,
                    "Sorry, I couldn't re-categorise that. Please re-upload the receipt."
                )
            return

        # ── AWAITING_REPORT_YEAR ───────────────────────────────────────────
        if conversation_state == "AWAITING_REPORT_YEAR":
            fy_end_month = user.get("financial_year_end_month") or 2
            if body.strip().isdigit():
                fy_year = int(body.strip())
                await _set_conversation_state(user_id, None)
                await _deliver_report(from_number, user_id, fy_end_month, fy_year)
            else:
                available_years = await report_queries.fetch_available_fy_years(
                    user_id, fy_end_month
                )
                year_list = "\n".join(f"  *{y}*" for y in available_years)
                twilio_client.send_whatsapp(
                    from_number, f"Please reply with a valid year:\n\n{year_list}"
                )
            return

        # ------------------------------------------------------------------
        # Keyword commands
        # ------------------------------------------------------------------

        # YES / NO — confirm or flag a DRAFT entry
        confirmation = _parse_confirmation(body)
        if confirmation in ("YES", "NO"):
            draft_entry_id = await _find_draft_entry(conn, user_id)
            if draft_entry_id is None:
                twilio_client.send_whatsapp(from_number, _MSG_NO_PENDING)
                return

            if confirmation == "YES":
                try:
                    await _post_draft_entry(conn, draft_entry_id)
                    twilio_client.send_whatsapp(
                        from_number,
                        "✅ Saved to your books.\n\nReply *EDIT* within 24 hours if you need to change anything."
                    )
                except Exception:
                    log.exception("Failed to post entry %s", draft_entry_id)
                    twilio_client.send_whatsapp(
                        from_number, "Sorry, I couldn't save that entry. Please try again."
                    )
            else:
                # NO → enter edit flow
                await _enter_edit_flow(conn, user_id, draft_entry_id, from_number)
            return

        # EDIT — correct the most recently recorded entry
        if body.strip().upper() == "EDIT":
            entry_id = await _find_draft_entry(conn, user_id) \
                    or await _find_last_posted_entry(conn, user_id)
            if entry_id is None:
                twilio_client.send_whatsapp(
                    from_number,
                    "No recent entry found to edit. Send a receipt to get started."
                )
                return
            await _enter_edit_flow(conn, user_id, entry_id, from_number)
            return

        # CASH SALE — manual income entry with no document
        cmd = body.strip().upper()
        if cmd in ("CASH SALE", "CASHSALE", "SALE"):
            await conn.execute(
                """
                UPDATE users SET
                    conversation_state = 'AWAITING_CASH_SALE_DESCRIPTION'::conversation_state,
                    updated_at         = NOW()
                WHERE id = $1
                """,
                user_id,
            )
            twilio_client.send_whatsapp(from_number, _MSG_ASK_CASH_SALE_DESCRIPTION)
            return

        # Your Books commands
        if cmd == "BALANCE":
            twilio_client.send_whatsapp(from_number, await _cmd_balance(conn, user_id))
            return
        if cmd == "LAST":
            twilio_client.send_whatsapp(from_number, await _cmd_last(conn, user_id))
            return
        if cmd == "TOP":
            twilio_client.send_whatsapp(from_number, await _cmd_top(conn, user_id))
            return
        if cmd == "PENDING":
            twilio_client.send_whatsapp(from_number, await _cmd_pending(conn, user_id))
            return

        # REPORT keyword
        report_parts = body.strip().upper().split()
        if report_parts and report_parts[0] == "REPORT":
            fy_end_month = user.get("financial_year_end_month") or 2

            if len(report_parts) == 2 and report_parts[1].isdigit():
                fy_year = int(report_parts[1])
                await _deliver_report(from_number, user_id, fy_end_month, fy_year)
                return

            available_years = await report_queries.fetch_available_fy_years(
                user_id, fy_end_month
            )
            if not available_years:
                twilio_client.send_whatsapp(
                    from_number,
                    "No posted transactions found yet.\n\n"
                    "Upload a receipt and confirm it to see your financial statements."
                )
                return

            year_list = "\n".join(f"  *{y}*" for y in available_years)
            await _set_conversation_state(user_id, "AWAITING_REPORT_YEAR")
            twilio_client.send_whatsapp(
                from_number,
                f"📊 Which financial year would you like a report for?\n\n"
                f"{year_list}\n\n"
                f"Reply with the year (e.g. *{available_years[0]}*)."
            )
            return

        # HELP command or fall-through for unrecognised text
        twilio_client.send_whatsapp(from_number, _MSG_HELP)

    except Exception as exc:
        log.exception("Unhandled error processing message from %s", from_number)
        try:
            try:
                pool = await get_pool()
                async with pool.acquire() as _conn:
                    await _conn.execute(
                        "UPDATE users SET conversation_state = NULL, pending_entry_id = NULL, updated_at = NOW() "
                        "WHERE whatsapp_number = $1",
                        from_number,
                    )
            except Exception:
                log.exception("Could not reset conversation_state for %s", from_number)

            exc_str = str(exc).lower()
            if "does not balance" in exc_str:
                fallback_msg = (
                    "I couldn't record that receipt — the amounts didn't add up correctly. "
                    "Please try sending it again, ideally as a clearer photo or PDF."
                )
            elif "could not extract a valid total" in exc_str or "no expensedocuments" in exc_str:
                fallback_msg = (
                    "I couldn't read the total on that document. "
                    "Please make sure the full receipt is visible and try again."
                )
            elif "popia consent" in exc_str:
                fallback_msg = (
                    "It looks like your account isn't fully set up yet. "
                    "Please reply *YES* to give consent and get started."
                )
            elif "s3" in exc_str or "upload" in exc_str:
                fallback_msg = "I had trouble saving your document. Please try sending it again."
            elif "textract" in exc_str or "analyze_expense" in exc_str:
                fallback_msg = (
                    "I had trouble reading that document. "
                    "Please send a clearer photo or a PDF and try again."
                )
            else:
                fallback_msg = "Sorry, something went wrong on our end. Please try again in a moment."

            twilio_client.send_whatsapp(from_number, fallback_msg)
        except Exception:
            log.exception("Failed to send fallback error message to %s", from_number)
    finally:
        if conn is not None and pool is not None:
            await pool.release(conn)


# ---------------------------------------------------------------------------
# DB sub-handlers (kept at bottom to reduce scrolling overhead)
# ---------------------------------------------------------------------------

async def _ensure_user(conn: asyncpg.Connection, whatsapp_number: str) -> dict:
    row = await conn.fetchrow(
        "SELECT id, popia_consent_given, onboarding_step, financial_year_end_month, conversation_state FROM users WHERE whatsapp_number = $1",
        whatsapp_number,
    )
    if row:
        return dict(row)

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
    await conn.execute(
        """
        INSERT INTO accounts (user_id, code, name, account_type, normal_balance, parent_id, ifrs_line_item)
        SELECT $1, t.code, t.name, t.account_type, t.normal_balance, NULL, t.ifrs_line_item
        FROM account_templates t
        ON CONFLICT (user_id, code) DO NOTHING
        """,
        user_id,
    )
    log.info("Seeded Chart of Accounts for user %s", user_id)


async def _save_business_name(conn: asyncpg.Connection, user_id: UUID, business_name: str) -> None:
    await conn.execute(
        "UPDATE users SET business_name=$2, onboarding_step='PROVINCE', updated_at=NOW() WHERE id=$1",
        user_id, business_name.strip(),
    )


async def _save_tax_ref(conn: asyncpg.Connection, user_id: UUID, tax_ref: str | None) -> None:
    await conn.execute(
        "UPDATE users SET income_tax_ref=$2, onboarding_step='DONE', updated_at=NOW() WHERE id=$1",
        user_id, tax_ref,
    )
