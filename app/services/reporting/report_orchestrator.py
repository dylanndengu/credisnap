"""
Report orchestrator — coordinates query → PDF build → S3 upload → pre-signed URL.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import asyncpg
import boto3

from app.services.reporting import pdf_builder, report_queries
from app.services.reporting.statement_generator import current_financial_year

log = logging.getLogger(__name__)


@dataclass
class ReportResult:
    presigned_url: str
    business_name: str
    from_date: date
    to_date: date


async def generate_and_deliver(
    conn: asyncpg.Connection,
    user_id: UUID,
    fy_end_month: int,
) -> ReportResult | None:
    """
    Full pipeline: fetch data → build PDF → upload to S3 → return pre-signed URL.

    Returns None if there is no data to report.
    """
    from_date, to_date = current_financial_year(fy_end_month)

    data = await report_queries.fetch_report_data(conn, user_id, from_date, to_date)

    if not report_queries.has_any_data(data):
        return None

    pdf_bytes = pdf_builder.build_pdf(data)
    log.info("Built PDF report (%d bytes) for user %s", len(pdf_bytes), user_id)

    bucket = os.environ["S3_BUCKET"]
    region = os.environ.get("AWS_REGION", "af-south-1")
    key = f"reports/{user_id}/{to_date.isoformat()}-financial-report.pdf"

    s3 = boto3.client("s3", region_name=region)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=pdf_bytes,
        ContentType="application/pdf",
        ServerSideEncryption="AES256",
        ContentDisposition=f'attachment; filename="credisnap-report-{to_date.isoformat()}.pdf"',
    )

    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=86400,  # 24 hours — POPIA data minimisation
    )
    log.info("Generated pre-signed URL for user %s (expires 24h)", user_id)

    return ReportResult(
        presigned_url=url,
        business_name=data.user.get("business_name", "Your Business"),
        from_date=from_date,
        to_date=to_date,
    )
