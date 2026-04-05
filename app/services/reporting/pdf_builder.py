"""
PDF report builder using ReportLab Platypus.

Takes a FullReportData instance and returns the PDF as bytes.

Sections:
  1. Cover page
  2. Trial Balance
  3. General Ledger
  4. Profit & Loss
  5. Balance Sheet
  6. VAT201 Summary + Detail
  7. Vendor Statements of Account
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.reporting.report_queries import (
    FullReportData,
    GeneralLedgerAccount,
    TrialBalanceLine,
    Vat201DetailLine,
    Vat201Period,
    VendorStatement,
)
from app.services.reporting.statement_generator import BalanceSheet, ProfitAndLoss

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
_DARK_BLUE   = colors.HexColor("#1B3A5C")
_MID_BLUE    = colors.HexColor("#2E6DA4")
_LIGHT_GREY  = colors.HexColor("#F2F4F7")
_ALT_ROW     = colors.HexColor("#EAF0F8")
_RED         = colors.HexColor("#C0392B")
_GREEN       = colors.HexColor("#1A7A4A")
_WHITE       = colors.white
_BLACK       = colors.black

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
_BASE = getSampleStyleSheet()

def _style(name: str, **kwargs) -> ParagraphStyle:
    return ParagraphStyle(name, parent=_BASE["Normal"], **kwargs)

_S = {
    "cover_title":    _style("cover_title",    fontSize=22, textColor=_DARK_BLUE,
                             alignment=TA_CENTER, spaceAfter=6, fontName="Helvetica-Bold"),
    "cover_sub":      _style("cover_sub",      fontSize=13, textColor=_MID_BLUE,
                             alignment=TA_CENTER, spaceAfter=4),
    "cover_body":     _style("cover_body",     fontSize=10, textColor=_BLACK,
                             alignment=TA_CENTER, spaceAfter=4),
    "cover_note":     _style("cover_note",     fontSize=8,  textColor=colors.grey,
                             alignment=TA_CENTER, spaceAfter=2),
    "section_head":   _style("section_head",   fontSize=13, textColor=_DARK_BLUE,
                             fontName="Helvetica-Bold", spaceBefore=12, spaceAfter=4),
    "sub_head":       _style("sub_head",       fontSize=10, textColor=_MID_BLUE,
                             fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=3),
    "body":           _style("body",           fontSize=9,  spaceAfter=2),
    "body_right":     _style("body_right",     fontSize=9,  alignment=TA_RIGHT),
    "footer":         _style("footer",         fontSize=7,  textColor=colors.grey,
                             alignment=TA_CENTER),
    "balance_ok":     _style("balance_ok",     fontSize=9,  textColor=_GREEN,
                             fontName="Helvetica-Bold"),
    "balance_bad":    _style("balance_bad",    fontSize=9,  textColor=_RED,
                             fontName="Helvetica-Bold"),
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
W, H = A4
MARGIN = 1.5 * cm
CONTENT_W = W - 2 * MARGIN


def _zar(amount: Decimal, decimals: int = 2) -> str:
    if amount < 0:
        return f"(R {abs(amount):,.{decimals}f})"
    return f"R {amount:,.{decimals}f}"


def _dt(d: date) -> str:
    return d.strftime("%d %b %Y")


def _tbl_style(extra: list | None = None) -> TableStyle:
    base = [
        ("BACKGROUND",  (0, 0), (-1, 0),  _DARK_BLUE),
        ("TEXTCOLOR",   (0, 0), (-1, 0),  _WHITE),
        ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_WHITE, _ALT_ROW]),
        ("GRID",        (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if extra:
        base.extend(extra)
    return TableStyle(base)


def _hr() -> HRFlowable:
    return HRFlowable(width="100%", thickness=1, color=_MID_BLUE, spaceAfter=4)


def _page_header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.grey)

    biz = getattr(doc, "_biz_name", "CrediSnap Report")
    canvas.drawString(MARGIN, H - MARGIN + 0.3 * cm, f"{biz}  |  CONFIDENTIAL")
    canvas.drawRightString(W - MARGIN, H - MARGIN + 0.3 * cm, "CrediSnap")

    today = date.today().strftime("%d %b %Y")
    canvas.drawString(MARGIN, 0.6 * cm,
                      f"Generated {today} — POPIA compliant. Link expires 24 h.")
    canvas.drawRightString(W - MARGIN, 0.6 * cm, f"Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _cover(data: FullReportData) -> list:
    biz = data.user.get("business_name", "Your Business")
    vat = data.user.get("vat_number")
    tax = data.user.get("income_tax_ref")
    cipc = data.user.get("cipc_reg_number")

    story = [Spacer(1, 3 * cm)]
    story.append(Paragraph("FINANCIAL REPORT", _S["cover_title"]))
    story.append(Paragraph(biz, _S["cover_sub"]))
    story.append(Spacer(1, 0.5 * cm))
    story.append(_hr())
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        f"Period: {_dt(data.from_date)} – {_dt(data.to_date)}", _S["cover_body"]
    ))
    if vat:
        story.append(Paragraph(f"VAT Registration No: {vat}", _S["cover_body"]))
    if tax:
        story.append(Paragraph(f"SARS Income Tax Ref: {tax}", _S["cover_body"]))
    if cipc:
        story.append(Paragraph(f"CIPC Reg No: {cipc}", _S["cover_body"]))
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph(
        "Prepared in accordance with IFRS for SMEs (IASB 2015).", _S["cover_note"]
    ))
    story.append(Paragraph(
        "SARS record-keeping compliant — TAA s29 (5 years) / Companies Act s24 (7 years).",
        _S["cover_note"]
    ))
    story.append(Spacer(1, 0.5 * cm))

    contents = [
        "• Trial Balance",
        "• General Ledger",
        "• Profit & Loss Statement",
        "• Balance Sheet",
        "• VAT201 Summary",
        "• Vendor Statements of Account",
    ]
    for item in contents:
        story.append(Paragraph(item, _S["cover_body"]))

    story.append(PageBreak())
    return story


def _trial_balance(data: FullReportData) -> list:
    story = [
        Paragraph("TRIAL BALANCE", _S["section_head"]),
        Paragraph(
            f"For the period {_dt(data.from_date)} to {_dt(data.to_date)}",
            _S["body"]
        ),
        _hr(),
    ]

    if not data.trial_balance:
        story.append(Paragraph("No posted transactions in this period.", _S["body"]))
        story.append(PageBreak())
        return story

    rows = [["Code", "Account Name", "Type", "Debits (R)", "Credits (R)", "Balance (R)"]]
    total_dr = Decimal(0)
    total_cr = Decimal(0)

    for line in data.trial_balance:
        rows.append([
            line.code,
            line.name,
            line.account_type.title(),
            _zar(line.total_debits),
            _zar(line.total_credits),
            _zar(line.balance),
        ])
        total_dr += line.total_debits
        total_cr += line.total_credits

    # Totals row
    rows.append(["", "TOTAL", "", _zar(total_dr), _zar(total_cr), ""])

    col_w = [1.2*cm, 6.5*cm, 2*cm, 2.8*cm, 2.8*cm, 2.8*cm]
    tbl = Table(rows, colWidths=col_w, repeatRows=1)
    tbl.setStyle(_tbl_style([
        ("FONTNAME",   (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), _LIGHT_GREY),
        ("ALIGN",      (3, 0),  (-1, -1), "RIGHT"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 0.3 * cm))

    in_balance = abs(total_dr - total_cr) < Decimal("0.01")
    if in_balance:
        story.append(Paragraph("✓ Trial balance is in balance.", _S["balance_ok"]))
    else:
        diff = abs(total_dr - total_cr)
        story.append(Paragraph(
            f"⚠ OUT OF BALANCE — difference: {_zar(diff)}", _S["balance_bad"]
        ))

    story.append(PageBreak())
    return story


def _general_ledger(data: FullReportData) -> list:
    story = [
        Paragraph("GENERAL LEDGER", _S["section_head"]),
        Paragraph(
            f"All posted transactions {_dt(data.from_date)} to {_dt(data.to_date)}",
            _S["body"]
        ),
        _hr(),
    ]

    if not data.general_ledger:
        story.append(Paragraph("No posted transactions in this period.", _S["body"]))
        story.append(PageBreak())
        return story

    for acct in data.general_ledger:
        story.append(Paragraph(f"{acct.code}  {acct.name}", _S["sub_head"]))

        rows = [["Date", "Description", "Vendor", "Debit (R)", "Credit (R)", "Balance (R)"]]

        # Opening balance row
        if acct.opening_balance != 0:
            rows.append([
                "", "Opening Balance", "", "", "", _zar(acct.opening_balance)
            ])

        for line in acct.lines:
            rows.append([
                _dt(line.entry_date),
                line.description[:55] + ("…" if len(line.description) > 55 else ""),
                (line.vendor_name or "")[:20],
                _zar(line.debit)  if line.debit  else "—",
                _zar(line.credit) if line.credit else "—",
                _zar(line.running_balance),
            ])

        # Closing balance row
        rows.append(["", "Closing Balance", "", "", "", _zar(acct.closing_balance)])

        col_w = [1.8*cm, 6*cm, 2.5*cm, 2.3*cm, 2.3*cm, 2.3*cm]
        tbl = Table(rows, colWidths=col_w, repeatRows=1)
        tbl.setStyle(_tbl_style([
            ("ALIGN",      (3, 0), (-1, -1), "RIGHT"),
            ("FONTNAME",   (0, -1), (-1, -1), "Helvetica-Bold"),
            ("BACKGROUND", (0, -1), (-1, -1), _LIGHT_GREY),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 0.4 * cm))

    story.append(PageBreak())
    return story


def _pl(pl: ProfitAndLoss) -> list:
    story = [
        Paragraph("PROFIT & LOSS STATEMENT", _S["section_head"]),
        Paragraph(
            f"For the period {_dt(pl.period_start)} to {_dt(pl.period_end)}",
            _S["body"]
        ),
        _hr(),
    ]

    def section(title: str, lines, total_label: str, total: Decimal):
        if not lines:
            return
        story.append(Paragraph(title, _S["sub_head"]))
        rows = [[Paragraph(l.name, _S["body"]), Paragraph(_zar(l.balance), _S["body_right"])]
                for l in lines]
        rows.append([
            Paragraph(f"<b>{total_label}</b>", _S["body"]),
            Paragraph(f"<b>{_zar(total)}</b>", _S["body_right"]),
        ])
        tbl = Table(rows, colWidths=[CONTENT_W * 0.72, CONTENT_W * 0.28])
        tbl.setStyle(TableStyle([
            ("LINEABOVE",  (0, -1), (-1, -1), 0.5, _MID_BLUE),
            ("FONTSIZE",   (0, 0),  (-1, -1), 9),
            ("TOPPADDING", (0, 0),  (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 0.2 * cm))

    section("Revenue", pl.revenue + pl.other_income, "Total Revenue", pl.total_revenue)
    section("Cost of Sales", pl.cost_of_sales, "Total Cost of Sales", pl.total_cost_of_sales)

    story.append(Paragraph(
        f"<b>Gross Profit: {_zar(pl.gross_profit)}</b>", _S["sub_head"]
    ))
    section("Operating Expenses", pl.operating_expenses,
            "Total Operating Expenses", pl.total_operating_expenses)
    section("Finance Costs", pl.finance_costs, "Total Finance Costs", pl.total_finance_costs)

    colour = _GREEN if pl.net_profit >= 0 else _RED
    story.append(Spacer(1, 0.2 * cm))
    net_style = _style("net_profit_val", fontSize=11, fontName="Helvetica-Bold",
                       textColor=colour)
    story.append(Paragraph(f"Net Profit: {_zar(pl.net_profit)}", net_style))
    story.append(PageBreak())
    return story


def _balance_sheet(bs: BalanceSheet) -> list:
    story = [
        Paragraph("BALANCE SHEET", _S["section_head"]),
        Paragraph(f"As at {_dt(bs.as_at)}", _S["body"]),
        _hr(),
    ]

    def section(title: str, lines, total_label: str, total: Decimal):
        if not lines:
            return
        story.append(Paragraph(title, _S["sub_head"]))
        rows = [[Paragraph(l.name, _S["body"]), Paragraph(_zar(l.balance), _S["body_right"])]
                for l in lines]
        rows.append([
            Paragraph(f"<b>{total_label}</b>", _S["body"]),
            Paragraph(f"<b>{_zar(total)}</b>", _S["body_right"]),
        ])
        tbl = Table(rows, colWidths=[CONTENT_W * 0.72, CONTENT_W * 0.28])
        tbl.setStyle(TableStyle([
            ("LINEABOVE",  (0, -1), (-1, -1), 0.5, _MID_BLUE),
            ("FONTSIZE",   (0, 0),  (-1, -1), 9),
            ("TOPPADDING", (0, 0),  (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 0.2 * cm))

    section("Current Assets", bs.current_assets, "Total Current Assets",
            sum((l.balance for l in bs.current_assets), Decimal(0)))
    section("Non-Current Assets", bs.non_current_assets, "Total Non-Current Assets",
            sum((l.balance for l in bs.non_current_assets), Decimal(0)))
    story.append(Paragraph(f"<b>Total Assets: {_zar(bs.total_assets)}</b>", _S["sub_head"]))
    story.append(Spacer(1, 0.3 * cm))

    section("Current Liabilities", bs.current_liabilities, "Total Current Liabilities",
            sum((l.balance for l in bs.current_liabilities), Decimal(0)))
    section("Non-Current Liabilities", bs.non_current_liabilities,
            "Total Non-Current Liabilities",
            sum((l.balance for l in bs.non_current_liabilities), Decimal(0)))
    story.append(Paragraph(
        f"<b>Total Liabilities: {_zar(bs.total_liabilities)}</b>", _S["sub_head"]
    ))
    story.append(Spacer(1, 0.3 * cm))

    section("Equity", bs.equity, "Total Equity", bs.total_equity)
    story.append(Paragraph(f"<b>Net Assets: {_zar(bs.net_assets)}</b>", _S["sub_head"]))
    story.append(PageBreak())
    return story


def _vat201(periods: list[Vat201Period], detail: list[Vat201DetailLine]) -> list:
    story = [
        Paragraph("VAT201 SUMMARY", _S["section_head"]),
        Paragraph("SARS bi-monthly VAT periods — Input VAT claimed on purchases", _S["body"]),
        _hr(),
    ]

    if not periods:
        story.append(Paragraph("No VAT transactions in this period.", _S["body"]))
        story.append(PageBreak())
        return story

    # Summary table
    rows = [["VAT Period", "Output Net", "Output VAT", "Input Net", "Input VAT", "Net Payable"]]
    for p in periods:
        rows.append([
            _dt(p.tax_period),
            _zar(p.output_net),
            _zar(p.output_vat),
            _zar(p.input_net),
            _zar(p.input_vat),
            _zar(p.net_vat_payable),
        ])
    col_w = [2.5*cm, 2.8*cm, 2.5*cm, 2.8*cm, 2.5*cm, 2.8*cm]
    tbl = Table(rows, colWidths=col_w, repeatRows=1)
    tbl.setStyle(_tbl_style([("ALIGN", (1, 0), (-1, -1), "RIGHT")]))
    story.append(tbl)
    story.append(Spacer(1, 0.5 * cm))

    # Detail table
    if detail:
        story.append(Paragraph("VAT Transaction Detail", _S["sub_head"]))
        drows = [["Date", "Type", "Code", "Counterparty", "Invoice", "Net (R)", "VAT (R)", "Gross (R)"]]
        for d in detail:
            drows.append([
                _dt(d.entry_date),
                d.transaction_type,
                d.vat_code,
                (d.counterparty_name or "—")[:22],
                (d.invoice_number or "—")[:12],
                _zar(d.net_amount),
                _zar(d.vat_amount),
                _zar(d.gross_amount),
            ])
        dcol_w = [1.8*cm, 1.3*cm, 1*cm, 3.5*cm, 1.8*cm, 2.2*cm, 2*cm, 2.2*cm]
        dtbl = Table(drows, colWidths=dcol_w, repeatRows=1)
        dtbl.setStyle(_tbl_style([("ALIGN", (5, 0), (-1, -1), "RIGHT")]))
        story.append(dtbl)

    story.append(PageBreak())
    return story


def _vendor_statements(vendors: list[VendorStatement]) -> list:
    story = [
        Paragraph("VENDOR STATEMENTS OF ACCOUNT", _S["section_head"]),
        Paragraph("Spend history per supplier based on posted receipts and invoices.", _S["body"]),
        _hr(),
    ]

    if not vendors:
        story.append(Paragraph("No vendor transactions in this period.", _S["body"]))
        return story

    for vendor in vendors:
        story.append(Paragraph(vendor.vendor_name, _S["sub_head"]))
        rows = [["Date", "Description", "Invoice No.", "VAT (R)", "Gross (R)"]]
        for t in vendor.transactions:
            rows.append([
                _dt(t.entry_date),
                t.description[:50] + ("…" if len(t.description) > 50 else ""),
                t.invoice_number or "—",
                _zar(t.vat_amount) if t.vat_amount else "—",
                _zar(t.gross_amount),
            ])
        rows.append([
            "", f"<b>Total spend: {_zar(vendor.total_spend)}</b>", "", "", "",
        ])
        col_w = [1.8*cm, 7*cm, 2.2*cm, 2.2*cm, 2.2*cm]
        tbl = Table(rows, colWidths=col_w, repeatRows=1)
        tbl.setStyle(_tbl_style([
            ("ALIGN",    (3, 0),  (-1, -1), "RIGHT"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("SPAN",     (0, -1), (1, -1)),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 0.4 * cm))

    return story


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_pdf(data: FullReportData) -> bytes:
    """Build the full financial report PDF and return it as bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=MARGIN,
        leftMargin=MARGIN,
        topMargin=2 * cm,
        bottomMargin=1.5 * cm,
    )
    # Attach business name so the header/footer callback can read it
    doc._biz_name = data.user.get("business_name", "CrediSnap")

    story: list[Any] = []
    story += _cover(data)
    story += _trial_balance(data)
    story += _general_ledger(data)
    story += _pl(data.profit_and_loss)
    story += _balance_sheet(data.balance_sheet)
    story += _vat201(data.vat201_periods, data.vat201_detail)
    story += _vendor_statements(data.vendor_statements)

    doc.build(story, onFirstPage=_page_header_footer, onLaterPages=_page_header_footer)
    return buffer.getvalue()
