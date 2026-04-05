"""
OCR → Categorisation → Ledger pipeline orchestrator.

Entry point: process_document(document_id, raw_textract_json)

Sequence:
  1. Load document + user from DB, validate POPIA consent
  2. Parse Textract JSON → TextractExpense
  3. LLM categorise → CategorisedExpense
  4. Write journal entry → UUID
  5. Update document status (POSTED or EXTRACTED pending review)
  6. On any failure: mark document FAILED and re-raise

All DB state changes go through a single asyncpg connection so step 5
and any rollback are always consistent with the journal write.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

import anthropic
import asyncpg

from app.db.connection import get_pool
from app.services.categorisation import llm_categoriser
from app.services.ledger import journal_writer
from app.services.ocr import textract_parser

log = logging.getLogger(__name__)


async def process_document(document_id: UUID, raw_textract_json: dict) -> UUID:
    """
    Run the full pipeline for a single uploaded document.

    Args:
        document_id:       UUID of the documents row (status must be EXTRACTED).
        raw_textract_json: Full boto3 analyze_expense() response dict.

    Returns:
        The UUID of the created journal_entry (DRAFT or POSTED).

    Raises:
        ValueError          — invalid document state or missing data.
        asyncpg.PostgresError — DB constraint violations (bubble up for retry logic).
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # ------------------------------------------------------------------
        # 1. Load document and user
        # ------------------------------------------------------------------
        row = await conn.fetchrow(
            """
            SELECT d.id, d.user_id, d.status, u.popia_consent_given,
                   u.vat_number IS NOT NULL AS is_vat_registered
            FROM   documents d
            JOIN   users     u ON u.id = d.user_id
            WHERE  d.id = $1
            """,
            document_id,
        )

        if row is None:
            raise ValueError(f"Document {document_id} not found")

        if not row["popia_consent_given"]:
            raise ValueError(
                f"User {row['user_id']} has not given POPIA consent — cannot process document"
            )

        if row["status"] not in ("EXTRACTED", "PENDING"):
            raise ValueError(
                f"Document {document_id} has status {row['status']!r}; expected EXTRACTED or PENDING"
            )

        user_id: UUID = row["user_id"]

        # ------------------------------------------------------------------
        # 2. Mark document as PROCESSING
        # ------------------------------------------------------------------
        await conn.execute(
            "UPDATE documents SET status = 'PROCESSING', updated_at = NOW() WHERE id = $1",
            document_id,
        )

        try:
            # --------------------------------------------------------------
            # 3. Parse Textract JSON
            # --------------------------------------------------------------
            expense_raw = textract_parser.parse(raw_textract_json)

            # Write extracted metadata back to the document row
            await conn.execute(
                """
                UPDATE documents SET
                    ocr_raw_json          = $2,
                    extracted_data        = $3,
                    extraction_confidence = $4,
                    vendor_name           = $5,
                    document_date         = $6,
                    document_ref          = $7,
                    gross_amount          = $8,
                    vat_amount            = $9,
                    net_amount            = $10,
                    status                = 'EXTRACTED',
                    updated_at            = NOW()
                WHERE id = $1
                """,
                document_id,
                json.dumps(expense_raw.raw_json),
                json.dumps({"line_items": [li.model_dump() for li in expense_raw.line_items]}, default=str),
                expense_raw.ocr_confidence,
                expense_raw.vendor_name,
                expense_raw.document_date,
                expense_raw.invoice_number,
                float(expense_raw.gross_total),
                float(expense_raw.tax_amount) if expense_raw.tax_amount else None,
                float(expense_raw.gross_total - expense_raw.tax_amount)
                    if expense_raw.tax_amount else None,
            )

            # --------------------------------------------------------------
            # 4. Fetch valid account codes for this user (passed to LLM validator)
            # --------------------------------------------------------------
            code_rows = await conn.fetch(
                "SELECT code FROM accounts WHERE user_id = $1 AND is_active = TRUE",
                user_id,
            )
            valid_codes = {r["code"] for r in code_rows}

            # --------------------------------------------------------------
            # 5. LLM categorisation
            # --------------------------------------------------------------
            expense_categorised = llm_categoriser.categorise(
                expense=expense_raw,
                valid_account_codes=valid_codes,
                anthropic_client=anthropic.Anthropic(),
            )

            # --------------------------------------------------------------
            # 5b. Normalise line item amounts to gross if Textract returned net
            # --------------------------------------------------------------
            expense_categorised = expense_categorised.with_grossed_up_line_items()

            # --------------------------------------------------------------
            # 6. Write journal entry
            # --------------------------------------------------------------
            entry_id = await journal_writer.write(
                conn=conn,
                user_id=user_id,
                document_id=document_id,
                expense=expense_categorised,
            )

            # --------------------------------------------------------------
            # 7. Mark document POSTED (entry auto-posted) or back to EXTRACTED
            #    (entry is DRAFT, awaiting WhatsApp confirmation)
            # --------------------------------------------------------------
            final_doc_status = (
                "POSTED"
                if expense_categorised.combined_confidence >= journal_writer.AUTO_POST_THRESHOLD
                else "EXTRACTED"
            )
            await conn.execute(
                "UPDATE documents SET status = $2, updated_at = NOW() WHERE id = $1",
                document_id,
                final_doc_status,
            )

            log.info(
                "Document %s processed → journal entry %s (%s)",
                document_id, entry_id, final_doc_status,
            )
            return entry_id

        except Exception as exc:
            # Mark the document FAILED and store the error message
            await conn.execute(
                """
                UPDATE documents SET
                    status        = 'FAILED',
                    error_message = $2,
                    retry_count   = retry_count + 1,
                    updated_at    = NOW()
                WHERE id = $1
                """,
                document_id,
                str(exc),
            )
            log.exception("Pipeline failed for document %s", document_id)
            raise
