"""
Document classifier — determines whether an uploaded document is a PURCHASE
(we are the buyer) or a SALE (we are the seller/issuer).

Returns a (DocumentType | None, confidence) tuple:
  - (PURCHASE, 0.92) — confident purchase
  - (SALE,     0.95) — confident sale
  - (None,     0.45) — uncertain; caller should ask the user to clarify

Classification strategy:
  1. Heuristic: if the Textract-extracted vendor name contains enough significant
     words from the user's business name → confident SALE (no LLM needed).
  2. LLM tool-use (claude-haiku): for everything else; LLM returns type +
     confidence.  If confidence < UNCERTAIN_THRESHOLD → return (None, conf).
  3. Safe default: if the LLM call fails entirely → (PURCHASE, 0.5) so the
     document is processed as a purchase rather than dropped.

Callers that receive (None, ...) should pause processing and ask the user.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import anthropic

from app.models.extraction import DocumentType, TextractExpense

log = logging.getLogger(__name__)

# If classifier confidence is below this, we ask the user instead of guessing.
UNCERTAIN_THRESHOLD = 0.70

# Words too generic to use as business-name identifiers
_STOP_WORDS = frozenset({
    "pty", "ltd", "cc", "inc", "npc", "soc", "the", "and", "or", "of",
    "sa", "za", "&", "group", "holdings", "trading", "services",
    "solutions", "enterprises", "investments",
})

_TOOL_NAME = "classify_document"
_TOOL = {
    "name": _TOOL_NAME,
    "description": (
        "Classify a South African business document as PURCHASE, SALE, or UNCERTAIN.\n"
        "PURCHASE: the business received this document (they are the buyer).\n"
        "SALE: the business issued this document (they are the seller).\n"
        "UNCERTAIN: genuinely cannot tell from the available information."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "document_type": {
                "type": "string",
                "enum": ["PURCHASE", "SALE", "UNCERTAIN"],
                "description": "Classification result.",
            },
            "confidence": {
                "type": "number",
                "description": (
                    "0.0–1.0 confidence in this classification. "
                    "Use < 0.70 when genuinely uncertain."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence explaining the classification.",
            },
        },
        "required": ["document_type", "confidence", "reasoning"],
    },
}

_SYSTEM = """\
You are a document classifier for a South African bookkeeping system.

Given extracted fields from a business document, classify it:

PURCHASE — the business is the BUYER (supplier receipts / invoices they received)
SALE     — the business is the SELLER (sales invoices they issued to customers)
UNCERTAIN — genuinely cannot tell (e.g. vendor name missing, document unclear)

Key signals:
- If the vendor/issuer on the document matches the business name → SALE
- Standard retail receipts (fuel, supermarket, utilities, restaurant) → PURCHASE
- If the "Bill To" customer matches the business name → PURCHASE
- Missing or unreadable vendor → UNCERTAIN

Be honest about uncertainty — it is better to ask the user than to guess wrong.
"""


def _significant_words(name: str) -> list[str]:
    return [
        w.lower() for w in name.replace("(", " ").replace(")", " ").split()
        if w.lower() not in _STOP_WORDS and len(w) > 3
    ]


def classify(
    expense: TextractExpense,
    business_name: str | None,
    anthropic_client: anthropic.Anthropic | None = None,
) -> tuple[DocumentType | None, float]:
    """
    Classify a parsed document as PURCHASE, SALE, or uncertain.

    Returns:
        (DocumentType.PURCHASE, confidence) — confident purchase
        (DocumentType.SALE,     confidence) — confident sale
        (None,                  confidence) — uncertain; caller should ask the user
    """
    vendor = (expense.vendor_name or "").strip()

    # ── Heuristic: strong business-name match in vendor field ─────────────
    if business_name and vendor:
        biz_words = _significant_words(business_name)
        vendor_lower = vendor.lower()
        matches = sum(1 for w in biz_words if w in vendor_lower)
        threshold = 1 if len(biz_words) <= 2 else 2
        if matches >= threshold:
            log.info(
                "Document classified as SALE (heuristic: %d/%d business-name words "
                "in vendor %r)", matches, len(biz_words), vendor
            )
            return DocumentType.SALE, 0.95

    # ── LLM classification ────────────────────────────────────────────────
    client = anthropic_client or anthropic.Anthropic()
    prompt = (
        f"Business name: {business_name or 'Unknown'}\n"
        f"Vendor / Issuer on document: {vendor or 'Unknown'}\n"
        f"Invoice / receipt number: {expense.invoice_number or 'N/A'}\n"
        f"Total amount: R{expense.gross_total}\n"
        f"Sample line items: {', '.join(li.description for li in expense.line_items[:4])}"
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=_SYSTEM,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": prompt}],
        )
        tool_block = next(
            (b for b in response.content if b.type == "tool_use" and b.name == _TOOL_NAME),
            None,
        )
        if tool_block is None:
            log.warning("Classifier LLM returned no tool block; defaulting to PURCHASE")
            return DocumentType.PURCHASE, 0.5

        result     = tool_block.input.get("document_type", "UNCERTAIN")
        confidence = float(tool_block.input.get("confidence", 0.5))
        reasoning  = tool_block.input.get("reasoning", "")
        log.info("Classifier LLM: %s (conf=%.2f) — %s", result, confidence, reasoning)

        if result == "UNCERTAIN" or confidence < UNCERTAIN_THRESHOLD:
            return None, confidence

        doc_type = DocumentType.SALE if result == "SALE" else DocumentType.PURCHASE
        return doc_type, confidence

    except Exception:
        log.warning("Classifier LLM call failed; defaulting to PURCHASE")
        return DocumentType.PURCHASE, 0.5
