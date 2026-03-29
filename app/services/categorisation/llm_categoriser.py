"""
LLM-based expense categoriser using the Anthropic SDK.

Sends all line items from a parsed receipt to Claude in a single tool-use call.
Returns a CategorisedExpense with each line item assigned:
  - an account_code from the user's Chart of Accounts
  - a VAT code (SR / ZR / EX / OP)

The LLM never writes to the DB. It only enriches the Pydantic model.
Account codes returned by the LLM are validated against the user's actual CoA
before the model is handed to the journal_writer.
"""

from __future__ import annotations

import logging
from datetime import date

import anthropic

from app.models.extraction import (
    CategorisedExpense,
    CategorisedLineItem,
    TextractExpense,
    VatCode,
)

log = logging.getLogger(__name__)

_FALLBACK_ACCOUNT_CODE = "6190"   # Sundry Expenses
_FALLBACK_VAT_CODE     = VatCode.SR

_TOOL_NAME = "categorise_line_items"

# Build the tool definition once at module level
_TOOL = {
    "name": _TOOL_NAME,
    "description": (
        "Assign a Chart of Accounts code and VAT treatment to each expense line item "
        "from a South African business receipt or invoice. "
        "Use account 6190 (Sundry Expenses) as the fallback for items you cannot "
        "confidently categorise. "
        "Apply VAT code SR (Standard Rate 15%) unless you have a specific reason to use "
        "ZR (Zero-Rated), EX (Exempt: residential rent, financial services, insurance), "
        "or OP (Out of Scope: payroll deductions, inter-company transfers)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "categorisations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index":        {"type": "integer", "description": "0-based index matching the input line item"},
                        "account_code": {"type": "string",  "description": "4-digit account code e.g. '6040'"},
                        "vat_code":     {"type": "string",  "enum": ["SR", "ZR", "EX", "OP"]},
                        "reasoning":    {"type": "string",  "description": "One sentence explaining the categorisation"},
                    },
                    "required": ["index", "account_code", "vat_code", "reasoning"],
                },
            },
            "overall_confidence": {
                "type": "number",
                "description": "0.0 – 1.0: your confidence across all categorisations. "
                               "Use < 0.85 when any item is ambiguous.",
            },
        },
        "required": ["categorisations", "overall_confidence"],
    },
}

_SYSTEM_PROMPT = """\
You are a chartered accountant specialising in South African SME bookkeeping.
You categorise business expenses into the correct General Ledger accounts
following IFRS for SMEs and SARS tax legislation.

Chart of Accounts reference (expense codes only):
5010  Purchases — Goods for Resale
5020  Direct Labour
5030  Freight Inwards
6010  Salaries and Wages
6020  Employer UIF Contribution
6030  Rent Expense
6040  Utilities — Electricity
6050  Utilities — Water
6060  Telephone and Internet
6070  Motor Vehicle Expenses
6080  Fuel and Oil
6090  Repairs and Maintenance
6100  Stationery and Printing
6110  Bank Charges               (VAT code: EX — financial services are exempt)
6120  Professional Fees — Accounting
6130  Professional Fees — Legal
6140  Insurance                  (VAT code: EX — short-term insurance is exempt in SA)
6150  Depreciation
6160  Advertising and Marketing
6170  Travel and Accommodation
6180  Interest Expense           (VAT code: EX — interest is exempt)
6190  Sundry Expenses            (fallback for unclassifiable items)

SA VAT rules:
- SR (15%): default for most B2B goods and services
- ZR (0%): basic foodstuffs, exports, certain agricultural inputs
- EX: financial services, residential rent, insurance, medical services
- OP: salaries, loan repayments, inter-entity transfers
"""


def _build_user_message(expense: TextractExpense) -> str:
    lines = [
        f"Vendor: {expense.vendor_name or 'Unknown'}",
        f"Date: {expense.document_date or 'Unknown'}",
        f"Gross Total: R{expense.gross_total:.2f}",
        "",
        "Line items to categorise:",
    ]
    for i, item in enumerate(expense.line_items):
        lines.append(f"  [{i}] {item.description!r}  —  R{item.gross_amount:.2f}")
    return "\n".join(lines)


def _validate_codes(
    categorisations: list[dict],
    valid_codes: set[str],
    n_items: int,
) -> list[dict]:
    """
    Ensure every categorisation has a valid account code.
    Falls back to 6190 / SR for missing or unrecognised codes.
    Fills in any missing indices so we always get n_items results.
    """
    by_index: dict[int, dict] = {c["index"]: c for c in categorisations}

    result = []
    for i in range(n_items):
        cat = by_index.get(i, {})
        code = cat.get("account_code", _FALLBACK_ACCOUNT_CODE)

        if code not in valid_codes:
            log.warning(
                "LLM returned unknown account code %r for item %d; falling back to 6190",
                code, i,
            )
            code = _FALLBACK_ACCOUNT_CODE

        result.append({
            "index":        i,
            "account_code": code,
            "vat_code":     cat.get("vat_code", _FALLBACK_VAT_CODE.value),
            "reasoning":    cat.get("reasoning", "Fallback categorisation"),
        })
    return result


def categorise(
    expense: TextractExpense,
    valid_account_codes: set[str],
    anthropic_client: anthropic.Anthropic | None = None,
) -> CategorisedExpense:
    """
    Categorise all line items in a single LLM tool-use call.

    Args:
        expense:              Parsed Textract output.
        valid_account_codes:  Set of account codes that exist in the user's CoA.
                              The LLM's output is validated against this set.
        anthropic_client:     Optional pre-built client (useful for testing/DI).

    Returns:
        CategorisedExpense ready for journal_writer.
    """
    client = anthropic_client or anthropic.Anthropic()

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": _build_user_message(expense)}],
    )

    # Extract tool use block
    tool_block = next(
        (b for b in response.content if b.type == "tool_use" and b.name == _TOOL_NAME),
        None,
    )
    if tool_block is None:
        log.error("LLM did not return a tool use block; using fallback categorisations")
        categorisations_raw = []
        llm_confidence = 0.0
    else:
        categorisations_raw = tool_block.input.get("categorisations", [])
        llm_confidence      = float(tool_block.input.get("overall_confidence", 0.5))

    validated = _validate_codes(categorisations_raw, valid_account_codes, len(expense.line_items))

    categorised_items = [
        CategorisedLineItem(
            description=expense.line_items[cat["index"]].description,
            account_code=cat["account_code"],
            vat_code=VatCode(cat["vat_code"]),
            gross_amount=expense.line_items[cat["index"]].gross_amount,
            llm_reasoning=cat["reasoning"],
        )
        for cat in validated
    ]

    return CategorisedExpense(
        vendor_name=expense.vendor_name,
        vendor_vat_number=expense.vendor_vat_number,
        document_date=expense.document_date or date.today(),
        invoice_number=expense.invoice_number,
        gross_total=expense.gross_total,
        ocr_confidence=expense.ocr_confidence,
        raw_json=expense.raw_json,
        line_items=categorised_items,
        llm_confidence=llm_confidence,
    )
