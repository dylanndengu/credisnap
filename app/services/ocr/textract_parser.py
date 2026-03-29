"""
AWS Textract AnalyzeExpense response parser.

Converts the raw Textract JSON into a TextractExpense Pydantic model.
The raw JSON is preserved unchanged — it is written to documents.ocr_raw_json
as the immutable SARS audit trail.

Textract AnalyzeExpense structure:
  response["ExpenseDocuments"][0]
    ["SummaryFields"]    — document-level fields (vendor, date, total, tax…)
    ["LineItemGroups"][*]["LineItems"][*]["LineItemExpenseFields"]  — line items
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from dateutil import parser as dateutil_parser

from app.models.extraction import RawLineItem, TextractExpense

log = logging.getLogger(__name__)

# Textract SummaryField type strings we care about
_VENDOR_NAME      = "VENDOR_NAME"
_VENDOR_VAT       = "VENDOR_VAT_NUMBER"    # or TAX_PAYER_ID on some docs
_TAX_PAYER_ID     = "TAX_PAYER_ID"
_DATE             = "INVOICE_RECEIPT_DATE"
_INVOICE_ID       = "INVOICE_RECEIPT_ID"
_TOTAL            = "TOTAL"
_TAX              = "TAX"

# LineItemExpenseField type strings
_ITEM             = "ITEM"
_DESCRIPTION      = "DESCRIPTION"
_PRICE            = "PRICE"         # line total (quantity × unit price)
_UNIT_PRICE       = "UNIT_PRICE"


def _clean_amount(text: str) -> Decimal | None:
    """Strip currency symbols and thousands separators, return Decimal or None."""
    cleaned = text.replace("R", "").replace("ZAR", "").replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _extract_summary_fields(fields: list[dict]) -> dict[str, tuple[str, float]]:
    """
    Build a dict mapping field-type → (value_text, confidence).
    When duplicate types appear, keep the highest-confidence occurrence.
    """
    result: dict[str, tuple[str, float]] = {}
    for field in fields:
        field_type = field.get("Type", {}).get("Text", "")
        value_det  = field.get("ValueDetection", {})
        text       = value_det.get("Text", "").strip()
        confidence = float(value_det.get("Confidence", 0.0)) / 100.0  # Textract uses 0-100

        if not text:
            continue
        if field_type not in result or confidence > result[field_type][1]:
            result[field_type] = (text, confidence)
    return result


def _parse_line_items(line_item_groups: list[dict]) -> list[tuple[RawLineItem, float]]:
    """
    Return list of (RawLineItem, confidence) tuples.
    Skips lines where no amount can be parsed.
    """
    items: list[tuple[RawLineItem, float]] = []

    for group in line_item_groups:
        for line in group.get("LineItems", []):
            fields: dict[str, tuple[str, float]] = {}
            for f in line.get("LineItemExpenseFields", []):
                ftype      = f.get("Type", {}).get("Text", "")
                value_det  = f.get("ValueDetection", {})
                text       = value_det.get("Text", "").strip()
                confidence = float(value_det.get("Confidence", 0.0)) / 100.0
                if text:
                    fields[ftype] = (text, confidence)

            # Prefer PRICE (line total) over UNIT_PRICE
            amount_text, amount_conf = fields.get(_PRICE) or fields.get(_UNIT_PRICE) or ("", 0.0)
            amount = _clean_amount(amount_text)
            if amount is None or amount <= 0:
                continue

            # Prefer DESCRIPTION over ITEM for the label
            desc_text, desc_conf = fields.get(_DESCRIPTION) or fields.get(_ITEM) or ("Unknown item", 0.0)

            line_conf = min(amount_conf, desc_conf) if desc_conf else amount_conf
            items.append((RawLineItem(description=desc_text, gross_amount=amount), line_conf))

    return items


def parse(raw_response: dict[str, Any]) -> TextractExpense:
    """
    Parse a Textract AnalyzeExpense response dict into a TextractExpense.

    Args:
        raw_response: The full dict returned by boto3 analyze_expense().

    Returns:
        TextractExpense with all extractable fields populated.
        ocr_confidence is the minimum confidence across all used fields.
    """
    docs = raw_response.get("ExpenseDocuments", [])
    if not docs:
        raise ValueError("Textract response contains no ExpenseDocuments")

    # Use the first document (single receipt per call)
    doc = docs[0]
    summary = _extract_summary_fields(doc.get("SummaryFields", []))

    confidence_scores: list[float] = []

    def get(key: str) -> str | None:
        entry = summary.get(key)
        if entry:
            confidence_scores.append(entry[1])
            return entry[0]
        return None

    # Vendor name
    vendor_name = get(_VENDOR_NAME)

    # VAT number — Textract may use VENDOR_VAT_NUMBER or TAX_PAYER_ID
    vendor_vat = get(_VENDOR_VAT) or get(_TAX_PAYER_ID)

    # Date — use dateutil to handle any format Textract returns
    document_date = None
    date_str = get(_DATE)
    if date_str:
        try:
            document_date = dateutil_parser.parse(date_str, dayfirst=True).date()
        except Exception:
            log.warning("Could not parse document date: %r", date_str)

    invoice_number = get(_INVOICE_ID)

    # Amounts
    gross_total_str = get(_TOTAL)
    gross_total     = _clean_amount(gross_total_str) if gross_total_str else None
    if gross_total is None or gross_total <= 0:
        raise ValueError(f"Could not extract a valid TOTAL from Textract response: {gross_total_str!r}")

    tax_str    = get(_TAX)
    tax_amount = _clean_amount(tax_str) if tax_str else None

    # Line items
    line_item_tuples = _parse_line_items(doc.get("LineItemGroups", []))
    line_items   = [item for item, _ in line_item_tuples]
    line_confs   = [conf for _, conf in line_item_tuples]
    confidence_scores.extend(line_confs)

    # If Textract returned no line items, synthesise one from the total
    # so the pipeline can still attempt categorisation.
    if not line_items:
        log.warning("No line items parsed from Textract; synthesising single line from TOTAL")
        line_items = [RawLineItem(
            description=vendor_name or "Unknown expense",
            gross_amount=gross_total,
        )]

    ocr_confidence = min(confidence_scores) if confidence_scores else 0.5

    return TextractExpense(
        vendor_name=vendor_name,
        vendor_vat_number=vendor_vat,
        document_date=document_date,
        invoice_number=invoice_number,
        gross_total=gross_total,
        tax_amount=tax_amount,
        line_items=line_items,
        ocr_confidence=ocr_confidence,
        raw_json=raw_response,
    )
