"""
Bulk receipt ingestion script.

Feeds a folder of PDF/image files directly into the CrediSnap pipeline,
bypassing WhatsApp. Useful for testing with generated or real receipts.

Usage:
    python scripts/bulk_ingest.py <folder> <whatsapp_number>

    <folder>           — path to a directory containing PDF/JPG/PNG files
    <whatsapp_number>  — E.164 number of an existing consented user e.g. +27821234567

Options:
    --dry-run   — validate files and check user exists, but don't process
    --delay N   — seconds to wait between documents (default: 1)
                  increase if hitting Textract or Anthropic rate limits

Example:
    python scripts/bulk_ingest.py receipts/ +27821234567
    python scripts/bulk_ingest.py receipts/ +27821234567 --delay 2
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

# ---------------------------------------------------------------------------
# Bootstrap: load .env and set up Python path before any app imports
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))

env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

import asyncpg

from app.db.connection import get_pool, close_pool
from app.whatsapp.media_handler import upload_to_s3, analyze_expense
from app.pipeline import process_document

# ---------------------------------------------------------------------------
# Supported MIME types
# ---------------------------------------------------------------------------
_MIME = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".pdf":  "application/pdf",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_files(folder: Path) -> list[Path]:
    files = sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in _MIME
    )
    return files


async def _get_user(conn: asyncpg.Connection, whatsapp_number: str) -> dict | None:
    row = await conn.fetchrow(
        "SELECT id, business_name, popia_consent_given FROM users WHERE whatsapp_number = $1",
        whatsapp_number,
    )
    return dict(row) if row else None


async def _create_document_row(
    conn: asyncpg.Connection,
    user_id: UUID,
    mime_type: str,
    file_size: int,
    filename: str,
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO documents
            (user_id, s3_bucket, s3_key, mime_type, file_size_bytes,
             whatsapp_message_id, status)
        VALUES ($1, '', '', $2, $3, $4, 'PENDING')
        RETURNING id
        """,
        user_id,
        mime_type,
        file_size,
        f"bulk_ingest:{filename}",
    )


async def _process_file(
    conn: asyncpg.Connection,
    user_id: UUID,
    file_path: Path,
    index: int,
    total: int,
) -> dict:
    """Process a single file through the full pipeline. Returns a result dict."""
    mime_type = _MIME[file_path.suffix.lower()]
    content   = file_path.read_bytes()
    result    = {"file": file_path.name, "status": None, "entry_id": None, "confidence": None, "error": None}

    try:
        # Create document row
        document_id = await _create_document_row(
            conn, user_id, mime_type, len(content), file_path.name
        )

        # Upload to S3
        bucket, key, etag = upload_to_s3(content, mime_type, user_id, document_id)
        await conn.execute(
            "UPDATE documents SET s3_bucket=$2, s3_key=$3, s3_etag=$4, updated_at=NOW() WHERE id=$1",
            document_id, bucket, key, etag,
        )

        # Textract
        raw_textract = analyze_expense(bucket, key)

        # Full pipeline
        entry_id = await process_document(document_id, raw_textract)

        # Check outcome
        status_row = await conn.fetchrow(
            "SELECT status, ai_confidence FROM journal_entries WHERE id = $1", entry_id
        )
        result["status"]     = status_row["status"] if status_row else "UNKNOWN"
        result["entry_id"]   = str(entry_id)
        result["confidence"] = status_row["ai_confidence"] if status_row else None

    except Exception as exc:
        result["status"] = "FAILED"
        result["error"]  = str(exc)

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(folder: Path, whatsapp_number: str, dry_run: bool, delay: float):
    files = _collect_files(folder)
    if not files:
        print(f"No supported files found in {folder}")
        sys.exit(1)

    print(f"\n── CrediSnap Bulk Ingest ─────────────────────────")
    print(f"  Folder  : {folder}")
    print(f"  Files   : {len(files)}")
    print(f"  User    : {whatsapp_number}")
    print(f"  Dry run : {dry_run}")
    print()

    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await _get_user(conn, whatsapp_number)

        if user is None:
            print(f"❌ User {whatsapp_number} not found. Have they completed onboarding?")
            sys.exit(1)

        if not user["popia_consent_given"]:
            print(f"❌ User {whatsapp_number} has not given POPIA consent.")
            sys.exit(1)

        print(f"  Business: {user['business_name']}")
        print()

        if dry_run:
            print("DRY RUN — files that would be processed:")
            for f in files:
                print(f"  {f.name}  ({_MIME[f.suffix.lower()]})")
            return

        results = []
        for i, file_path in enumerate(files, 1):
            print(f"[{i:3}/{len(files)}] {file_path.name} ...", end=" ", flush=True)
            result = await _process_file(conn, user["id"], file_path, i, len(files))
            results.append(result)

            if result["status"] == "FAILED":
                print(f"❌ FAILED — {result['error'][:80]}")
            else:
                conf_str = f"{result['confidence']:.2f}" if result["confidence"] is not None else "n/a"
                print(f"✅ {result['status']}  (conf={conf_str}, entry {result['entry_id'][:8]}…)")

            if i < len(files):
                time.sleep(delay)

    # Summary
    posted  = sum(1 for r in results if r["status"] == "POSTED")
    draft   = sum(1 for r in results if r["status"] == "DRAFT")
    failed  = sum(1 for r in results if r["status"] == "FAILED")

    print()
    print("── Summary ───────────────────────────────────────")
    print(f"  Total   : {len(results)}")
    print(f"  Posted  : {posted}  (high confidence, auto-posted)")
    print(f"  Draft   : {draft}   (low confidence, needs review)")
    print(f"  Failed  : {failed}")

    if failed:
        print()
        print("Failed files:")
        for r in results:
            if r["status"] == "FAILED":
                print(f"  {r['file']}: {r['error'][:100]}")

    await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk ingest receipts into CrediSnap")
    parser.add_argument("folder", type=Path, help="Folder containing PDF/JPG/PNG files")
    parser.add_argument("whatsapp_number", help="E.164 number of an existing user e.g. +27821234567")
    parser.add_argument("--dry-run", action="store_true", help="Validate only, don't process")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between documents (default: 1)")
    args = parser.parse_args()

    if not args.folder.is_dir():
        print(f"❌ {args.folder} is not a directory")
        sys.exit(1)

    asyncio.run(main(args.folder, args.whatsapp_number, args.dry_run, args.delay))
