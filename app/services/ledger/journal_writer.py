"""
Journal writer — the only component that touches the database.

Takes a CategorisedExpense and writes atomically:
  1. journal_entries        (one header row)
  2. journal_entry_lines    (DR expense × n, DR VAT Input × n, CR Bank × 1)
  3. vat_entries            (one row per SR/ZR line item)
  4. UPDATE journal_entries status → POSTED  (if confidence >= AUTO_POST_THRESHOLD)

The DB trigger trg_journal_entry_balance fires on step 4 and rejects the
status update if debits ≠ credits. Everything is inside a single transaction
so a trigger failure rolls back all inserts automatically.

Key invariants enforced here (matching DB constraints):
  - debit_or_credit_not_both: each line is DR>0,CR=0 or DR=0,CR>0
  - gross = net + vat in vat_entries (guaranteed by CategorisedLineItem.derive_net_and_vat)
  - Generated columns period_month / period_year are NOT included in INSERT
  - vat_journal_line_id is captured via RETURNING id — no separate SELECT
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from uuid import UUID

import asyncpg

from app.models.extraction import CategorisedExpense, VatCode

log = logging.getLogger(__name__)

AUTO_POST_THRESHOLD = 0.85

# SA bi-monthly VAT period start months: Jan, Mar, May, Jul, Sep, Nov
_VAT_PERIOD_START = {1: 1, 2: 1, 3: 3, 4: 3, 5: 5, 6: 5,
                     7: 7, 8: 7, 9: 9, 10: 9, 11: 11, 12: 11}


def _tax_period(tx_date: date) -> date:
    """Return the first day of the SA bi-monthly VAT period for a given date."""
    start_month = _VAT_PERIOD_START[tx_date.month]
    return date(tx_date.year, start_month, 1)


async def _fetch_account_map(conn: asyncpg.Connection, user_id: UUID) -> dict[str, UUID]:
    """
    Load all active accounts for a user into a code→UUID dict.
    Called once per write to avoid per-line round-trips.
    """
    rows = await conn.fetch(
        "SELECT code, id FROM accounts WHERE user_id = $1 AND is_active = TRUE",
        user_id,
    )
    return {row["code"]: row["id"] for row in rows}


async def write(
    conn: asyncpg.Connection,
    user_id: UUID,
    document_id: UUID,
    expense: CategorisedExpense,
    auto_post: bool = True,
) -> UUID:
    """
    Write a balanced journal entry for a categorised expense document.

    Returns the journal_entry UUID (DRAFT or POSTED depending on confidence).

    Raises:
        ValueError  — if line items do not sum to the document gross total,
                      or if the bank / VAT-input accounts are missing from the CoA.
        asyncpg.PostgresError — propagates DB errors (trigger violations, FK failures).
    """

    # -----------------------------------------------------------------------
    # Pre-flight: validate line item totals before touching the DB
    # -----------------------------------------------------------------------
    if not expense.validate_line_totals():
        log.warning(
            "Line item sum (R%s) ≠ document gross (R%s) for document %s — "
            "writing single-line DRAFT entry for manual review",
            expense.line_items_gross_total, expense.gross_total, document_id,
        )
        # Collapse to a single unclassified line so the entry is still created
        # but stays DRAFT. The user will be prompted via WhatsApp to confirm.
        from app.models.extraction import CategorisedLineItem
        expense = expense.model_copy(update={
            "line_items": [
                CategorisedLineItem(
                    description=f"Receipt: {expense.vendor_name or 'Unknown vendor'}",
                    account_code="6190",
                    vat_code=VatCode.SR,
                    gross_amount=expense.gross_total,
                )
            ],
            "llm_confidence": 0.0,   # Force DRAFT
        })

    # -----------------------------------------------------------------------
    # Resolve account codes → UUIDs (one query, no per-line round-trips)
    # -----------------------------------------------------------------------
    account_map = await _fetch_account_map(conn, user_id)

    bank_account_id = account_map.get("1020")
    vat_input_id    = account_map.get("1200")

    if bank_account_id is None:
        raise ValueError("Account 1020 (Business Bank Account) not found in Chart of Accounts")
    if vat_input_id is None:
        raise ValueError("Account 1200 (VAT Input) not found in Chart of Accounts")

    # -----------------------------------------------------------------------
    # All inserts + optional status update in a single transaction
    # -----------------------------------------------------------------------
    async with conn.transaction():

        # 1. Journal entry header (always DRAFT on insert)
        entry_id: UUID = await conn.fetchval(
            """
            INSERT INTO journal_entries
                (user_id, document_id, entry_date, description,
                 status, is_ai_generated, ai_confidence)
            VALUES ($1, $2, $3, $4, 'DRAFT', TRUE, $5)
            RETURNING id
            """,
            user_id,
            document_id,
            expense.document_date,
            f"Receipt: {expense.vendor_name or 'Unknown vendor'}",
            expense.combined_confidence,
        )

        line_order = 0

        # 2. Debit lines — one expense line + optional VAT line per item
        for item in expense.line_items:

            # Skip zero-value lines (would violate debit_or_credit_not_both constraint)
            if item.gross_amount <= 0:
                continue

            expense_account_id = account_map.get(item.account_code) or account_map.get("6190")
            if expense_account_id is None:
                raise ValueError(f"Account 6190 (Sundry) missing from Chart of Accounts")

            # DR expense account (net amount)
            await conn.execute(
                """
                INSERT INTO journal_entry_lines
                    (journal_entry_id, account_id, debit_amount, credit_amount,
                     description, line_order)
                VALUES ($1, $2, $3, 0, $4, $5)
                """,
                entry_id,
                expense_account_id,
                item.net_amount,
                item.description,
                line_order,
            )
            line_order += 1

            # DR VAT Input account (only if SR/ZR and vat_amount > 0)
            if item.vat_code.creates_vat_entry and item.vat_amount > 0:
                vat_line_id: UUID = await conn.fetchval(
                    """
                    INSERT INTO journal_entry_lines
                        (journal_entry_id, account_id, debit_amount, credit_amount,
                         vat_amount, vat_code, description, line_order)
                    VALUES ($1, $2, $3, 0, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    entry_id,
                    vat_input_id,
                    item.vat_amount,
                    item.vat_code.value,         # VARCHAR(5) column
                    f"VAT: {item.description}",
                    line_order,
                )
                line_order += 1

                # 3. VAT entry — links to the specific VAT journal line (RETURNING id above)
                vat_rate = Decimal("0.15") if item.vat_code == VatCode.SR else Decimal("0.00")

                await conn.execute(
                    """
                    INSERT INTO vat_entries
                        (user_id, journal_entry_id, vat_journal_line_id, document_id,
                         transaction_type, vat_code,
                         net_amount, vat_amount, gross_amount, vat_rate,
                         counterparty_name, counterparty_vat_number,
                         invoice_number, tax_period)
                    VALUES
                        ($1, $2, $3, $4,
                         'INPUT'::vat_transaction_type, $5::vat_code,
                         $6, $7, $8, $9,
                         $10, $11, $12, $13)
                    """,
                    user_id,
                    entry_id,
                    vat_line_id,
                    document_id,
                    item.vat_code.value,
                    item.net_amount,
                    item.vat_amount,
                    item.gross_amount,
                    vat_rate,
                    expense.vendor_name,
                    expense.vendor_vat_number,
                    expense.invoice_number,
                    _tax_period(expense.document_date),
                )

        # 4. CR Bank account (single line for the full gross total)
        await conn.execute(
            """
            INSERT INTO journal_entry_lines
                (journal_entry_id, account_id, debit_amount, credit_amount,
                 description, line_order)
            VALUES ($1, $2, 0, $3, $4, $5)
            """,
            entry_id,
            bank_account_id,
            expense.gross_total,
            f"Payment to {expense.vendor_name or 'Unknown vendor'}",
            line_order,
        )

        # 5. Auto-post if enabled and combined confidence meets threshold.
        #    The trg_journal_entry_balance trigger fires here and rejects
        #    the update if debits ≠ credits, rolling back the entire transaction.
        if auto_post and expense.combined_confidence >= AUTO_POST_THRESHOLD:
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                entry_id,
            )
            log.info("Journal entry %s auto-posted (confidence=%.2f)", entry_id, expense.combined_confidence)
        else:
            log.info(
                "Journal entry %s left as DRAFT (confidence=%.2f, auto_post=%s)",
                entry_id, expense.combined_confidence, auto_post,
            )

    return entry_id


async def write_sale(
    conn: asyncpg.Connection,
    user_id: UUID,
    document_id: UUID,
    expense: CategorisedExpense,
    auto_post: bool = True,
) -> UUID:
    """
    Write a balanced journal entry for a sales invoice (income document).

    DR Bank Account (1020)        — gross total received
    CR Revenue Account (4xxx)     — net amount per line item
    CR VAT Output Account (2100)  — output VAT collected per line item

    Raises:
        ValueError  — if required accounts are missing or line items don't balance.
        asyncpg.PostgresError — DB constraint violations.
    """
    if not expense.validate_line_totals():
        log.warning(
            "Sale line item sum (R%s) ≠ document gross (R%s) for document %s — "
            "writing single-line DRAFT entry for manual review",
            expense.line_items_gross_total, expense.gross_total, document_id,
        )
        expense = expense.model_copy(update={
            "line_items": [
                CategorisedLineItem(
                    description=f"Sales Invoice: {expense.invoice_number or expense.vendor_name or 'Unknown'}",
                    account_code="4020",
                    vat_code=VatCode.SR,
                    gross_amount=expense.gross_total,
                )
            ],
            "llm_confidence": 0.0,
        })

    account_map = await _fetch_account_map(conn, user_id)

    bank_account_id   = account_map.get("1020")   # DR Bank
    vat_output_id     = account_map.get("2100")   # CR VAT Output

    if bank_account_id is None:
        raise ValueError("Account 1020 (Business Bank Account) not found in Chart of Accounts")
    if vat_output_id is None:
        raise ValueError("Account 2100 (VAT Output) not found in Chart of Accounts")

    async with conn.transaction():

        # 1. Journal entry header (always DRAFT on insert)
        entry_id: UUID = await conn.fetchval(
            """
            INSERT INTO journal_entries
                (user_id, document_id, entry_date, description,
                 status, is_ai_generated, ai_confidence)
            VALUES ($1, $2, $3, $4, 'DRAFT', TRUE, $5)
            RETURNING id
            """,
            user_id,
            document_id,
            expense.document_date,
            f"Sales Invoice: {expense.invoice_number or expense.vendor_name or 'Customer'}",
            expense.combined_confidence,
        )

        line_order = 0

        # 2. DR Bank — single debit for the full gross amount received
        await conn.execute(
            """
            INSERT INTO journal_entry_lines
                (journal_entry_id, account_id, debit_amount, credit_amount,
                 description, line_order)
            VALUES ($1, $2, $3, 0, $4, $5)
            """,
            entry_id,
            bank_account_id,
            expense.gross_total,
            f"Receipt: {expense.invoice_number or 'Sales Invoice'}",
            line_order,
        )
        line_order += 1

        # 3. CR Revenue + CR VAT Output — one set of credits per line item
        for item in expense.line_items:
            if item.gross_amount <= 0:
                continue

            revenue_account_id = account_map.get(item.account_code) or account_map.get("4020")
            if revenue_account_id is None:
                raise ValueError("Account 4020 (Sales — Services) missing from Chart of Accounts")

            # CR Revenue account (net amount)
            await conn.execute(
                """
                INSERT INTO journal_entry_lines
                    (journal_entry_id, account_id, debit_amount, credit_amount,
                     description, line_order)
                VALUES ($1, $2, 0, $3, $4, $5)
                """,
                entry_id,
                revenue_account_id,
                item.net_amount,
                item.description,
                line_order,
            )
            line_order += 1

            # CR VAT Output (only if SR/ZR and vat_amount > 0)
            if item.vat_code.creates_vat_entry and item.vat_amount > 0:
                vat_line_id: UUID = await conn.fetchval(
                    """
                    INSERT INTO journal_entry_lines
                        (journal_entry_id, account_id, debit_amount, credit_amount,
                         vat_amount, vat_code, description, line_order)
                    VALUES ($1, $2, 0, $3, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    entry_id,
                    vat_output_id,
                    item.vat_amount,
                    item.vat_code.value,
                    f"VAT Output: {item.description}",
                    line_order,
                )
                line_order += 1

                vat_rate = Decimal("0.15") if item.vat_code == VatCode.SR else Decimal("0.00")
                await conn.execute(
                    """
                    INSERT INTO vat_entries
                        (user_id, journal_entry_id, vat_journal_line_id, document_id,
                         transaction_type, vat_code,
                         net_amount, vat_amount, gross_amount, vat_rate,
                         counterparty_name, counterparty_vat_number,
                         invoice_number, tax_period)
                    VALUES
                        ($1, $2, $3, $4,
                         'OUTPUT'::vat_transaction_type, $5::vat_code,
                         $6, $7, $8, $9,
                         $10, $11, $12, $13)
                    """,
                    user_id,
                    entry_id,
                    vat_line_id,
                    document_id,
                    item.vat_code.value,
                    item.net_amount,
                    item.vat_amount,
                    item.gross_amount,
                    vat_rate,
                    expense.vendor_name,       # our business — issuer of the invoice
                    expense.vendor_vat_number,
                    expense.invoice_number,
                    _tax_period(expense.document_date),
                )

        # 4. Auto-post if enabled and confidence meets threshold
        if auto_post and expense.combined_confidence >= AUTO_POST_THRESHOLD:
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                entry_id,
            )
            log.info("Sale entry %s auto-posted (confidence=%.2f)", entry_id, expense.combined_confidence)
        else:
            log.info(
                "Sale entry %s left as DRAFT (confidence=%.2f, auto_post=%s)",
                entry_id, expense.combined_confidence, auto_post,
            )

    return entry_id
