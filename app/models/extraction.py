"""
Pydantic models for the OCR → Categorisation → Ledger pipeline.

Data flows in one direction:
  TextractExpense  (parser output)
      ↓
  CategorisedExpense  (categoriser output — line items enriched with account + VAT codes)
      ↓
  journal_writer  (reads CategorisedExpense, writes to DB)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any

from pydantic import BaseModel, model_validator


_CENT = Decimal("0.01")
_VAT_RATE = Decimal("0.15")


class VatCode(str, Enum):
    SR = "SR"   # Standard Rate 15%  — generates a vat_entries row
    ZR = "ZR"   # Zero Rated 0%      — generates a vat_entries row (vat_amount = 0)
    EX = "EX"   # Exempt             — NO vat_entries row
    OP = "OP"   # Out of Scope       — NO vat_entries row

    @property
    def creates_vat_entry(self) -> bool:
        return self in (VatCode.SR, VatCode.ZR)


# ---------------------------------------------------------------------------
# Stage 1 output — raw Textract parse
# ---------------------------------------------------------------------------

class RawLineItem(BaseModel):
    """Single line item as extracted from Textract. Amounts are gross (incl. VAT)."""
    description: str
    gross_amount: Decimal


class TextractExpense(BaseModel):
    """
    Structured extraction from a Textract AnalyzeExpense response.
    All monetary values are in ZAR.
    """
    vendor_name: str | None = None
    vendor_vat_number: str | None = None   # SARS requires this for Input VAT claims > R50
    document_date: date | None = None
    invoice_number: str | None = None
    gross_total: Decimal                   # Total inc. VAT as printed on document
    tax_amount: Decimal | None = None      # VAT portion as extracted (may be absent)
    line_items: list[RawLineItem]
    ocr_confidence: float                  # min() across all extracted Textract fields
    raw_json: dict[str, Any]              # Full Textract response — never mutated, audit trail


# ---------------------------------------------------------------------------
# Stage 2 output — LLM-categorised line items
# ---------------------------------------------------------------------------

class CategorisedLineItem(BaseModel):
    """
    Line item after LLM categorisation. Net/VAT amounts are derived here
    using exact Decimal arithmetic to satisfy the DB gross = net + vat constraint.
    """
    description: str
    account_code: str       # e.g. "6040" — validated against user's CoA before DB write
    vat_code: VatCode
    gross_amount: Decimal   # as on receipt
    net_amount: Decimal     # derived: gross / 1.15 for SR, gross for others
    vat_amount: Decimal     # derived: gross - net (always exact by construction)
    llm_reasoning: str | None = None

    @model_validator(mode="before")
    @classmethod
    def derive_net_and_vat(cls, data: dict) -> dict:
        """
        Compute net and vat from gross + vat_code.
        Uses ROUND_HALF_UP then sets vat = gross - net so net + vat = gross exactly.
        This satisfies the DB CHECK: gross_amount = net_amount + vat_amount.
        """
        gross = Decimal(str(data["gross_amount"]))
        code = VatCode(data["vat_code"])

        if code == VatCode.SR:
            net = (gross / (1 + _VAT_RATE)).quantize(_CENT, ROUND_HALF_UP)
            vat = gross - net   # exact: guarantees net + vat = gross
        else:
            net = gross
            vat = Decimal("0.00")

        data["net_amount"] = net
        data["vat_amount"] = vat
        return data


class CategorisedExpense(BaseModel):
    """Full expense document after both parsing and LLM categorisation."""

    # Carried from TextractExpense
    vendor_name: str | None
    vendor_vat_number: str | None
    document_date: date
    invoice_number: str | None
    gross_total: Decimal
    ocr_confidence: float
    raw_json: dict[str, Any]

    # Set by categoriser
    line_items: list[CategorisedLineItem]
    llm_confidence: float           # overall_confidence returned by the LLM tool call

    @property
    def combined_confidence(self) -> float:
        """Weakest-link: entry is only as trustworthy as its least-confident step."""
        return min(self.ocr_confidence, self.llm_confidence)

    @property
    def line_items_gross_total(self) -> Decimal:
        return sum(item.gross_amount for item in self.line_items)

    def validate_line_totals(self) -> bool:
        """
        Returns True if line items sum to the document gross total (VAT-inclusive),
        OR if they sum to the net total (gross / 1.15) — Textract often returns net
        line prices when the receipt shows ex-VAT amounts with a bottom-line VAT entry.
        A 2-cent tolerance handles rounding across many line items.
        """
        _TOLERANCE = Decimal("0.02")
        line_sum = self.line_items_gross_total
        if abs(line_sum - self.gross_total) <= _TOLERANCE:
            return True
        net_total = (self.gross_total / (1 + _VAT_RATE)).quantize(_CENT, ROUND_HALF_UP)
        return abs(line_sum - net_total) <= _TOLERANCE

    def with_grossed_up_line_items(self) -> "CategorisedExpense":
        """
        If Textract returned net (ex-VAT) line item amounts, scale each item
        proportionally so they sum to gross_total. This ensures journal entries balance.
        If items already sum to gross_total, returns self unchanged.
        """
        _TOLERANCE = Decimal("0.02")
        line_sum = self.line_items_gross_total

        if abs(line_sum - self.gross_total) <= _TOLERANCE:
            return self  # already gross, nothing to do

        net_total = (self.gross_total / (1 + _VAT_RATE)).quantize(_CENT, ROUND_HALF_UP)
        if abs(line_sum - net_total) > _TOLERANCE:
            return self  # neither gross nor net match — don't guess, leave for manual review

        # Scale each item: new_gross = old_net_amount * (gross_total / net_total_sum)
        scale = self.gross_total / line_sum
        scaled_items = [
            CategorisedLineItem(
                description=item.description,
                account_code=item.account_code,
                vat_code=item.vat_code,
                gross_amount=(item.gross_amount * scale).quantize(_CENT, ROUND_HALF_UP),
                llm_reasoning=item.llm_reasoning,
            )
            for item in self.line_items
        ]

        # Fix any rounding residual on the last item so sum == gross_total exactly
        residual = self.gross_total - sum(i.gross_amount for i in scaled_items)
        if residual != Decimal("0"):
            last = scaled_items[-1]
            scaled_items[-1] = CategorisedLineItem(
                description=last.description,
                account_code=last.account_code,
                vat_code=last.vat_code,
                gross_amount=last.gross_amount + residual,
                llm_reasoning=last.llm_reasoning,
            )

        return self.model_copy(update={"line_items": scaled_items})
