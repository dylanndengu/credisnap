"""
CrediSnap Demo Script

Processes a curated set of sample receipts and invoices directly through
the full pipeline (S3 → Textract → Claude → Ledger) without going through
WhatsApp. Prints a formatted summary of each result — ideal for demos.

Usage:
  python demo.py                     # Run all demo files
  python demo.py --receipts-only     # Receipts only
  python demo.py --invoices-only     # Invoices only
  python demo.py --file sample_receipts/001_fuel_bp_01.pdf  # Single file
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
import uuid
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Load .env
for line in (Path(__file__).parent / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

import asyncpg
import boto3

from app.db.connection import get_pool
from app.whatsapp.media_handler import upload_to_s3, analyze_expense
from app.services.ocr import textract_parser
from app.services.categorisation import llm_categoriser
from app.pipeline import process_document
import anthropic

# ── Demo file selection ────────────────────────────────────────────────────
DEMO_RECEIPTS = [
    "sample_receipts/001_fuel_bp_01.pdf",
    "sample_receipts/001_fuel_engen_01.pdf",
    "sample_receipts/019_supermarket_spar_01.pdf",
    "sample_receipts/020_supermarket_woolworths_02.pdf",
    "sample_receipts/076_restaurant_nando's_01.pdf",
]

DEMO_INVOICES = [
    "sample_invoices/001_professional_services_deloitte_01.pdf",
    "sample_invoices/001_professional_services_grant_01.pdf",
    "sample_invoices/002_professional_services_bdo_02.pdf",
]


def _pick_existing(paths: list[str]) -> list[Path]:
    """Return only files that actually exist, falling back to first available."""
    root = Path(__file__).parent
    found = [root / p for p in paths if (root / p).exists()]
    if found:
        return found
    # Fallback: just grab the first few from each folder
    receipts  = sorted((root / "sample_receipts").glob("*.pdf"))[:3]
    invoices  = sorted((root / "sample_invoices").glob("*.pdf"))[:2]
    return receipts + invoices


async def _get_or_create_demo_user(conn: asyncpg.Connection) -> uuid.UUID:
    """Get or create a demo user for the pipeline."""
    demo_number = "+27000000000"
    row = await conn.fetchrow(
        "SELECT id FROM users WHERE whatsapp_number = $1", demo_number
    )
    if row:
        return row["id"]

    user_id = await conn.fetchval(
        """
        INSERT INTO users
            (whatsapp_number, business_name, popia_consent_given,
             popia_consent_at, popia_consent_version, data_retention_until)
        VALUES ($1, 'CrediSnap Demo Business', TRUE, NOW(), '1.0',
                (CURRENT_DATE + INTERVAL '5 years')::date)
        RETURNING id
        """,
        demo_number,
    )

    # Seed chart of accounts
    await conn.execute(
        """
        INSERT INTO accounts (user_id, code, name, account_type, normal_balance, ifrs_line_item, parent_id)
        SELECT $1, t.code, t.name, t.account_type, t.normal_balance, t.ifrs_line_item, NULL
        FROM   account_templates t
        ON CONFLICT DO NOTHING
        """,
        user_id,
    )
    return user_id


async def process_file(
    file_path: Path,
    user_id: uuid.UUID,
    conn: asyncpg.Connection,
    index: int,
    total: int,
) -> dict:
    """Upload, OCR, categorise, and write one file. Returns a result dict."""
    print(f"\n[{index}/{total}] {file_path.name}")
    print(f"  {'─' * 55}")

    content   = file_path.read_bytes()
    mime_type = "application/pdf"
    doc_id    = uuid.uuid4()

    # 1. Upload to S3
    print(f"  Uploading to S3...", end=" ", flush=True)
    bucket, key, _ = upload_to_s3(content, mime_type, user_id, doc_id)
    print("done")

    # 2. Create document row
    await conn.execute(
        """
        INSERT INTO documents
            (id, user_id, s3_bucket, s3_key, mime_type, file_size_bytes, status)
        VALUES ($1, $2, $3, $4, $5, $6, 'PENDING')
        """,
        doc_id, user_id, bucket, key, mime_type, len(content),
    )

    # 3. Textract
    print(f"  Running Textract OCR...", end=" ", flush=True)
    raw_textract = analyze_expense(bucket, key)
    expense_raw  = textract_parser.parse(raw_textract)
    print(f"done  (confidence: {expense_raw.ocr_confidence:.0%})")
    print(f"  Vendor : {expense_raw.vendor_name or 'Unknown'}")
    print(f"  Date   : {expense_raw.document_date or 'Unknown'}")
    print(f"  Total  : R{expense_raw.gross_total:,.2f}")

    # 4. Run full pipeline
    print(f"  Categorising with Claude...", end=" ", flush=True)
    entry_id = await process_document(doc_id, raw_textract)
    print("done")

    # 5. Fetch result
    if entry_id:
        entry = await conn.fetchrow(
            """
            SELECT je.status, je.ai_confidence, je.description,
                   COUNT(jel.id) AS line_count,
                   SUM(jel.debit_amount) AS total_debits
            FROM   journal_entries     je
            JOIN   journal_entry_lines jel ON jel.journal_entry_id = je.id
            WHERE  je.id = $1
            GROUP  BY je.id
            """,
            entry_id,
        )
        lines = await conn.fetch(
            """
            SELECT a.code, a.name,
                   jel.debit_amount, jel.credit_amount, jel.description
            FROM   journal_entry_lines jel
            JOIN   accounts            a   ON a.id = jel.account_id
            WHERE  jel.journal_entry_id = $1
            ORDER  BY jel.line_order
            """,
            entry_id,
        )

        status_icon = "✅ AUTO-POSTED" if entry["status"] == "POSTED" else "📋 DRAFT (awaiting confirmation)"
        print(f"\n  {status_icon}  (confidence: {entry['ai_confidence']:.0%})")
        print(f"  Journal Entry:")
        for line in lines:
            if line["debit_amount"] > 0:
                print(f"    DR {line['code']} {line['name']:<35} R{line['debit_amount']:>10,.2f}")
            else:
                print(f"    CR {line['code']} {line['name']:<35} R{line['credit_amount']:>10,.2f}")
        return {"file": file_path.name, "status": entry["status"], "entry_id": entry_id}
    else:
        print(f"  ⚠️  Document type unclear — would ask user via WhatsApp to clarify")
        return {"file": file_path.name, "status": "AWAITING_USER", "entry_id": None}


async def run_demo(files: list[Path]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await _get_or_create_demo_user(conn)
        print(f"\n{'═' * 60}")
        print(f"  CrediSnap Demo  —  {len(files)} document(s)")
        print(f"{'═' * 60}")

        results = []
        for i, f in enumerate(files, 1):
            try:
                result = await process_file(f, user_id, conn, i, len(files))
                results.append(result)
            except Exception as e:
                print(f"  ❌ Failed: {e}")
                results.append({"file": f.name, "status": "FAILED", "entry_id": None})

        # Summary
        posted  = sum(1 for r in results if r["status"] == "POSTED")
        draft   = sum(1 for r in results if r["status"] == "DRAFT")
        failed  = sum(1 for r in results if r["status"] == "FAILED")
        unclear = sum(1 for r in results if r["status"] == "AWAITING_USER")

        print(f"\n{'═' * 60}")
        print(f"  Summary: {len(results)} processed")
        print(f"    ✅ Auto-posted : {posted}")
        print(f"    📋 Draft       : {draft}")
        print(f"    ⚠️  Unclear     : {unclear}")
        print(f"    ❌ Failed      : {failed}")
        print(f"{'═' * 60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipts-only", action="store_true")
    parser.add_argument("--invoices-only", action="store_true")
    parser.add_argument("--file", type=str, help="Process a single file")
    args = parser.parse_args()

    root = Path(__file__).parent

    if args.file:
        files = [root / args.file]
    elif args.receipts_only:
        files = _pick_existing(DEMO_RECEIPTS)
    elif args.invoices_only:
        files = _pick_existing(DEMO_INVOICES)
    else:
        files = _pick_existing(DEMO_RECEIPTS + DEMO_INVOICES)

    files = [f for f in files if f.exists()]
    if not files:
        print("No files found. Check sample_receipts/ and sample_invoices/ directories.")
        sys.exit(1)

    asyncio.run(run_demo(files))
