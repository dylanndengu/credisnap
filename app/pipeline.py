"""
OCR → Classification → Categorisation → Ledger pipeline orchestrator.

Entry points:
  process_document(document_id, raw_textract_json)
      Full pipeline. Returns UUID of journal entry, or None if the document
      type is uncertain and the user must be asked to clarify.

  resume_document_with_type(document_id, doc_type)
      Resume a paused document (status=EXTRACTED, type unknown) after the user
      has confirmed whether it is a PURCHASE or SALE.

Sequence (process_document):
  1. Load document + user, validate POPIA consent
  2. Parse Textract JSON → TextractExpense, persist metadata
  3. Classify document type (PURCHASE / SALE / uncertain)
     → If uncertain: return None — caller sends WhatsApp and sets conversation state
  4. Categorise line items (expense or revenue categoriser)
  5. Normalise line item amounts to gross if Textract returned net values
  6. Write journal entry (purchase or sale writer)
  7. Update document status (POSTED or EXTRACTED pending review)
  8. On any failure: mark document FAILED and re-raise
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

import anthropic
import asyncpg

from app.db.connection import get_pool
from app.models.extraction import DocumentType, TextractExpense
from app.services.categorisation import llm_categoriser, revenue_categoriser
from app.services.classification import document_classifier
from app.services.ledger import journal_writer
from app.services.ocr import textract_parser

log = logging.getLogger(__name__)


async def process_document(
    document_id: UUID,
    raw_textract_json: dict,
    auto_post: bool = True,
) -> UUID | None:
    """
    Run the full pipeline for a single uploaded document.

    Returns:
        UUID of the created journal_entry on success.
        None if the document type is uncertain — the caller must ask the user.

    Raises:
        ValueError            — invalid document state or missing data.
        asyncpg.PostgresError — DB constraint violations.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT d.id, d.user_id, d.status, u.popia_consent_given, u.business_name
            FROM   documents d
            JOIN   users     u ON u.id = d.user_id
            WHERE  d.id = $1
            """,
            document_id,
        )

        if row is None:
            raise ValueError(f"Document {document_id} not found")
        if not row["popia_consent_given"]:
            raise ValueError(f"User {row['user_id']} has not given POPIA consent")
        if row["status"] not in ("EXTRACTED", "PENDING"):
            raise ValueError(
                f"Document {document_id} has status {row['status']!r}; expected EXTRACTED or PENDING"
            )

        user_id:       UUID = row["user_id"]
        business_name: str  = row["business_name"] or ""

        await conn.execute(
            "UPDATE documents SET status = 'PROCESSING', updated_at = NOW() WHERE id = $1",
            document_id,
        )

        try:
            # ── 2. Parse Textract ─────────────────────────────────────────
            expense_raw = textract_parser.parse(raw_textract_json)

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

            # ── 3. Classify document type ─────────────────────────────────
            anthropic_client = anthropic.Anthropic()
            doc_type, confidence = document_classifier.classify(
                expense=expense_raw,
                business_name=business_name,
                anthropic_client=anthropic_client,
            )

            if doc_type is None:
                # Uncertain — leave document as EXTRACTED, signal caller to ask user
                log.info(
                    "Document %s classification uncertain (conf=%.2f) — pausing for user input",
                    document_id, confidence,
                )
                return None

            return await _categorise_and_write(
                conn, user_id, document_id, expense_raw, doc_type, anthropic_client,
                auto_post=auto_post,
            )

        except Exception as exc:
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


async def resume_document_with_type(
    document_id: UUID,
    doc_type: DocumentType,
    auto_post: bool = True,
) -> UUID:
    """
    Resume processing a document whose type was confirmed by the user.

    Reads the already-stored ocr_raw_json, runs categorisation + journal write
    with the user-confirmed document type. Does not re-call Textract.

    Returns:
        UUID of the created journal_entry.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT d.id, d.user_id, d.status, d.ocr_raw_json, u.popia_consent_given
            FROM   documents d
            JOIN   users     u ON u.id = d.user_id
            WHERE  d.id = $1
            """,
            document_id,
        )

        if row is None:
            raise ValueError(f"Document {document_id} not found")
        if row["status"] not in ("EXTRACTED", "PROCESSING"):
            raise ValueError(
                f"Document {document_id} has unexpected status {row['status']!r}"
            )
        if not row["ocr_raw_json"]:
            raise ValueError(f"Document {document_id} has no stored OCR data to resume from")

        user_id = row["user_id"]
        raw_json = row["ocr_raw_json"]
        if isinstance(raw_json, str):
            raw_json = json.loads(raw_json)

        await conn.execute(
            "UPDATE documents SET status = 'PROCESSING', updated_at = NOW() WHERE id = $1",
            document_id,
        )

        try:
            expense_raw = textract_parser.parse(raw_json)

            # Persist the confirmed document type
            await conn.execute(
                "UPDATE documents SET document_type = $2::document_type, updated_at = NOW() WHERE id = $1",
                document_id,
                doc_type.value,
            )

            return await _categorise_and_write(
                conn, user_id, document_id, expense_raw, doc_type, anthropic.Anthropic(),
                auto_post=auto_post,
            )

        except Exception as exc:
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
            log.exception("Resume failed for document %s", document_id)
            raise


async def _categorise_and_write(
    conn: asyncpg.Connection,
    user_id: UUID,
    document_id: UUID,
    expense_raw: TextractExpense,
    doc_type: DocumentType,
    anthropic_client: anthropic.Anthropic,
    auto_post: bool = True,
) -> UUID:
    """
    Shared categorise → normalise → write path used by both process_document
    and resume_document_with_type.
    """
    # Persist confirmed document type
    await conn.execute(
        "UPDATE documents SET document_type = $2::document_type, updated_at = NOW() WHERE id = $1",
        document_id,
        doc_type.value,
    )

    code_rows = await conn.fetch(
        "SELECT code FROM accounts WHERE user_id = $1 AND is_active = TRUE",
        user_id,
    )
    valid_codes = {r["code"] for r in code_rows}

    if doc_type == DocumentType.SALE:
        expense_categorised = revenue_categoriser.categorise(
            expense=expense_raw,
            valid_account_codes=valid_codes,
            anthropic_client=anthropic_client,
        )
    else:
        expense_categorised = llm_categoriser.categorise(
            expense=expense_raw,
            valid_account_codes=valid_codes,
            anthropic_client=anthropic_client,
        )

    expense_categorised = expense_categorised.with_grossed_up_line_items()

    if doc_type == DocumentType.SALE:
        entry_id = await journal_writer.write_sale(
            conn=conn,
            user_id=user_id,
            document_id=document_id,
            expense=expense_categorised,
            auto_post=auto_post,
        )
    else:
        entry_id = await journal_writer.write(
            conn=conn,
            user_id=user_id,
            document_id=document_id,
            expense=expense_categorised,
            auto_post=auto_post,
        )

    final_doc_status = (
        "POSTED"
        if (auto_post and expense_categorised.combined_confidence >= journal_writer.AUTO_POST_THRESHOLD)
        else "EXTRACTED"
    )
    await conn.execute(
        "UPDATE documents SET status = $2, updated_at = NOW() WHERE id = $1",
        document_id,
        final_doc_status,
    )

    log.info(
        "Document %s (%s) → journal entry %s (%s)",
        document_id, doc_type, entry_id, final_doc_status,
    )
    return entry_id
