"""
Media pipeline: Twilio → S3 → Textract.

Three sequential steps:
  1. download_media  — fetch the image/PDF from Twilio's CDN (requires auth)
  2. upload_to_s3    — store in S3 under a deterministic key, server-side encrypted
  3. analyze_expense — call Textract AnalyzeExpense against the S3 object

S3 key format:  receipts/{user_id}/{document_id}{ext}
POPIA note: the S3 bucket must be in an AWS region that satisfies the POPIA
cross-border transfer requirements (af-south-1 Cape Town preferred).

Environment variables required:
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  S3_BUCKET
  AWS_REGION  (default: af-south-1)
"""

from __future__ import annotations

import logging
import mimetypes
import os
from uuid import UUID

import boto3
import httpx

log = logging.getLogger(__name__)

_ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "application/pdf",
}

# Map mimetypes library quirks to proper extensions
_EXT_OVERRIDES = {
    ".jpe": ".jpg",
    ".jpeg": ".jpg",
}


async def download_media(media_url: str, content_type: str) -> tuple[bytes, str]:
    """
    Download a media file from Twilio's CDN.

    Args:
        media_url:    The MediaUrl0 field from the Twilio webhook payload.
        content_type: The MediaContentType0 field.

    Returns:
        (raw_bytes, normalised_mime_type)

    Raises:
        ValueError if the content type is not in the allowed set.
    """
    mime = content_type.split(";")[0].strip().lower()
    if mime not in _ALLOWED_MIME_TYPES:
        raise ValueError(
            f"Unsupported media type {mime!r}. "
            f"Allowed: {', '.join(sorted(_ALLOWED_MIME_TYPES))}"
        )

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(
            media_url,
            auth=(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]),
        )
        response.raise_for_status()

    log.info("Downloaded %d bytes from Twilio (%s)", len(response.content), mime)
    return response.content, mime


def upload_to_s3(
    content: bytes,
    mime_type: str,
    user_id: UUID,
    document_id: UUID,
) -> tuple[str, str, str]:
    """
    Upload raw bytes to S3 with server-side encryption.

    Args:
        content:     Raw file bytes.
        mime_type:   MIME type string (e.g. 'image/jpeg').
        user_id:     Owning user UUID — used in the S3 key for logical partitioning.
        document_id: Document UUID — becomes the filename.

    Returns:
        (bucket, s3_key, etag)
    """
    ext = mimetypes.guess_extension(mime_type) or ".bin"
    ext = _EXT_OVERRIDES.get(ext, ext)

    bucket = os.environ["S3_BUCKET"]
    key    = f"receipts/{user_id}/{document_id}{ext}"
    region = os.environ.get("AWS_REGION", "af-south-1")

    s3 = boto3.client("s3", region_name=region)
    response = s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType=mime_type,
        ServerSideEncryption="AES256",   # POPIA: encrypt at rest
    )

    etag = response.get("ETag", "").strip('"')
    log.info("Uploaded s3://%s/%s (%d bytes)", bucket, key, len(content))
    return bucket, key, etag


def analyze_expense(bucket: str, key: str) -> dict:
    """
    Call AWS Textract AnalyzeExpense on an S3 object.

    Uses the synchronous API (suitable for images and small PDFs).
    For multi-page PDFs > 5 pages, replace with the async StartExpenseAnalysis job API.

    Returns:
        Full Textract response dict — passed unchanged to textract_parser.parse().
    """
    region   = os.environ.get("AWS_REGION", "af-south-1")
    textract = boto3.client("textract", region_name=region)

    log.info("Submitting s3://%s/%s to Textract AnalyzeExpense", bucket, key)
    response = textract.analyze_expense(
        Document={"S3Object": {"Bucket": bucket, "Name": key}}
    )
    log.info(
        "Textract returned %d expense document(s)",
        len(response.get("ExpenseDocuments", [])),
    )
    return response
