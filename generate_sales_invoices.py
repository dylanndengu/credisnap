"""
generate_sales_invoices.py — Generate 75 realistic South African sales tax invoices
                              showing income earned by the test business.

Usage:
    python generate_sales_invoices.py

Output:
    ./sample_sales_invoices/  — 75 tax invoices issued BY the test business TO customers,
                                covering consulting, IT services, training, and product sales.
"""

import os
import random
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)

# ---------------------------------------------------------------------------
# OUR BUSINESS (the seller — matches the test user)
# ---------------------------------------------------------------------------

OUR_BUSINESS = {
    "name":    "Baba Trading (Pty) Ltd",
    "vat":     "4500001234",
    "address": "Suite 4, 22 Fredman Drive, Sandton, 2196",
    "phone":   "011 234 9900",
    "email":   "accounts@babatrading.co.za",
    "bank":    "First National Bank",
    "account": "6299881234",
    "branch":  "250655",
    "acc_type":"Business Cheque",
}

# ---------------------------------------------------------------------------
# CUSTOMERS (the buyers)
# ---------------------------------------------------------------------------

CUSTOMERS = [
    {"name": "Sasol Limited",                        "vat": "4500010001", "address": "Sasol Place, 50 Katherine St, Sandton, 2196",          "contact": "AP Department"},
    {"name": "Standard Bank Group Ltd",              "vat": "4500010002", "address": "9 Simmonds Street, Johannesburg, 2001",                "contact": "Finance Department"},
    {"name": "Nedbank Group Ltd",                    "vat": "4500010003", "address": "Nedbank 135, 135 Rivonia Road, Sandton, 2196",          "contact": "Accounts Payable"},
    {"name": "FirstRand Bank Ltd",                   "vat": "4500010004", "address": "4 Merchant Place, Fredman Drive, Sandton, 2196",        "contact": "Finance"},
    {"name": "Vodacom Group (Pty) Ltd",              "vat": "4500010005", "address": "Vodacom Boulevard, Vodavalley Park, Midrand, 1685",     "contact": "Procurement"},
    {"name": "MTN Group Ltd",                        "vat": "4500010006", "address": "216 14th Avenue, Fairland, Roodepoort, 1724",           "contact": "AP Team"},
    {"name": "Pick n Pay Retailers (Pty) Ltd",       "vat": "4500010007", "address": "101 Rosmead Avenue, Kenilworth, Cape Town, 7708",       "contact": "Finance"},
    {"name": "Shoprite Holdings Ltd",                "vat": "4500010008", "address": "Cnr William Dabs & Old Paarl Roads, Brackenfell, 7560", "contact": "Accounts"},
    {"name": "Discovery Holdings Ltd",               "vat": "4500010009", "address": "1 Discovery Place, Sandton, 2196",                      "contact": "AP Department"},
    {"name": "Old Mutual Ltd",                       "vat": "4500010010", "address": "Mutualpark, Jan Smuts Drive, Pinelands, Cape Town, 7405","contact": "Finance"},
    {"name": "Multichoice Group Ltd",                "vat": "4500010011", "address": "144 Bram Fischer Drive, Randburg, 2194",                "contact": "Procurement"},
    {"name": "Tiger Brands Ltd",                     "vat": "4500010012", "address": "3010 William Nicol Drive, Bryanston, Sandton, 2021",    "contact": "Finance"},
    {"name": "Bidvest Group Ltd",                    "vat": "4500010013", "address": "18 Crescent Drive, Melrose Arch, Johannesburg, 2196",   "contact": "AP"},
    {"name": "Clicks Group Ltd",                     "vat": "4500010014", "address": "Clicks House, Kirstenhof, Cape Town, 7945",             "contact": "Accounts Payable"},
    {"name": "Momentum Metropolitan Holdings Ltd",   "vat": "4500010015", "address": "268 West Avenue, Centurion, 0157",                      "contact": "Finance Department"},
]

# ---------------------------------------------------------------------------
# SERVICES / PRODUCTS SOLD
# ---------------------------------------------------------------------------

SERVICE_LINES = {
    "consulting": [
        ("Business Strategy Consulting (per day)",          8500.00),
        ("Financial Advisory Services (per hour)",          1800.00),
        ("Management Consulting Retainer (monthly)",       25000.00),
        ("Project Management Services (per day)",           6500.00),
        ("Due Diligence Review",                           35000.00),
        ("Business Process Analysis & Optimisation",       18000.00),
        ("Market Entry Strategy Report",                   22000.00),
        ("Feasibility Study — New Market",                 28000.00),
        ("Organisational Restructuring Advisory",          42000.00),
        ("Regulatory Compliance Consulting",               15000.00),
    ],
    "it_services": [
        ("Software Development — Custom Module",           45000.00),
        ("IT Systems Integration (per project)",           38000.00),
        ("Cloud Migration Assessment",                     22000.00),
        ("Cybersecurity Audit & Report",                   28000.00),
        ("IT Support Retainer (monthly)",                   8500.00),
        ("Database Design & Implementation",               32000.00),
        ("Mobile App Development (phase 1)",               65000.00),
        ("Network Infrastructure Consultation",            18500.00),
        ("Data Analytics Dashboard",                       25000.00),
        ("API Integration Development",                    30000.00),
    ],
    "training": [
        ("Leadership Development Workshop (2 days)",        18000.00),
        ("Financial Literacy Training (per session)",        5500.00),
        ("Excel Advanced Training (half day)",               3800.00),
        ("SARS eFiling Workshop (per attendee)",             1800.00),
        ("Project Management Certification Prep (3 days)", 22000.00),
        ("Sales & Negotiation Skills Workshop",             12000.00),
        ("Customer Service Excellence Training",             8500.00),
        ("Digital Marketing Masterclass (1 day)",            9500.00),
        ("HR Compliance & Labour Law Training",             11000.00),
        ("Presentation & Communication Skills",              7500.00),
    ],
    "products": [
        ("Branded Corporate Merchandise (bulk order)",      15000.00),
        ("Office Furniture Supply — 10-desk set",           48000.00),
        ("Promotional Material Design & Print",              8500.00),
        ("Corporate Gifts Hamper Set (x20)",                12000.00),
        ("Safety Equipment Supply (PPE kit)",                6500.00),
        ("Cleaning Equipment & Supplies (monthly)",          4800.00),
        ("Ergonomic Office Chairs (x10)",                   22000.00),
        ("Laptop Stands & Peripherals (x15)",                9500.00),
        ("Office Signage & Branding",                       14000.00),
        ("Printer & Copier Supply (annual contract)",       38000.00),
    ],
}

VAT_RATE = Decimal("0.15")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rand_date(start_year: int = 2024) -> datetime:
    start = datetime(start_year, 1, 1)
    end   = datetime(2025, 12, 31)
    return start + timedelta(days=random.randint(0, (end - start).days))


def _vat(net: Decimal) -> Decimal:
    return (net * VAT_RATE).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _fmt(amount: Decimal) -> str:
    return f"R {amount:,.2f}"


# ---------------------------------------------------------------------------
# PDF builder
# ---------------------------------------------------------------------------

BASE_STYLES = getSampleStyleSheet()

def _style(name, **kw):
    return ParagraphStyle(name, parent=BASE_STYLES["Normal"], **kw)

DARK_BLUE  = colors.HexColor("#1B3A5C")
MID_BLUE   = colors.HexColor("#2E6DA4")
LIGHT_GREY = colors.HexColor("#F2F4F7")
ALT_ROW    = colors.HexColor("#EAF0F8")


def _build_invoice_pdf(
    invoice_number: str,
    invoice_date: datetime,
    due_date: datetime,
    customer: dict,
    lines: list[tuple[str, int, Decimal]],  # (description, qty, unit_price_net)
    output_path: str,
) -> None:
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=15*mm, bottomMargin=15*mm,
    )
    W = A4[0] - 30*mm

    S = {
        "title":    _style("t", fontSize=20, textColor=DARK_BLUE, fontName="Helvetica-Bold"),
        "sub":      _style("s", fontSize=9,  textColor=MID_BLUE),
        "label":    _style("l", fontSize=8,  textColor=colors.grey),
        "body":     _style("b", fontSize=9),
        "body_r":   _style("br", fontSize=9, alignment=TA_RIGHT),
        "bold":     _style("bo", fontSize=9, fontName="Helvetica-Bold"),
        "bold_r":   _style("bor", fontSize=9, fontName="Helvetica-Bold", alignment=TA_RIGHT),
        "total":    _style("tot", fontSize=11, fontName="Helvetica-Bold", textColor=DARK_BLUE, alignment=TA_RIGHT),
        "note":     _style("n", fontSize=7, textColor=colors.grey),
    }

    story = []

    # ── Header ──────────────────────────────────────────────────────────────
    header = Table(
        [[
            Paragraph(OUR_BUSINESS["name"], S["title"]),
            Paragraph("TAX INVOICE", _style("inv", fontSize=16, textColor=MID_BLUE,
                                            fontName="Helvetica-Bold", alignment=TA_RIGHT)),
        ]],
        colWidths=[W * 0.6, W * 0.4],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
    story.append(header)
    story.append(Spacer(1, 3*mm))
    story.append(HRFlowable(width="100%", thickness=1.5, color=DARK_BLUE))
    story.append(Spacer(1, 4*mm))

    # ── Seller / Invoice meta ────────────────────────────────────────────────
    seller_info = (
        f"{OUR_BUSINESS['address']}<br/>"
        f"Tel: {OUR_BUSINESS['phone']}  |  Email: {OUR_BUSINESS['email']}<br/>"
        f"VAT Reg No: {OUR_BUSINESS['vat']}"
    )
    meta_info = (
        f"<b>Invoice No:</b> {invoice_number}<br/>"
        f"<b>Invoice Date:</b> {invoice_date.strftime('%d %B %Y')}<br/>"
        f"<b>Due Date:</b> {due_date.strftime('%d %B %Y')}<br/>"
        f"<b>Payment Terms:</b> 30 days net"
    )
    info_row = Table(
        [[Paragraph(seller_info, S["body"]), Paragraph(meta_info, S["body"])]],
        colWidths=[W * 0.55, W * 0.45],
    )
    info_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(info_row)
    story.append(Spacer(1, 5*mm))

    # ── Bill To ──────────────────────────────────────────────────────────────
    bill_to = Table(
        [[
            Paragraph("BILL TO", _style("bt", fontSize=8, textColor=colors.white,
                                        fontName="Helvetica-Bold")),
        ]],
        colWidths=[W],
    )
    bill_to.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DARK_BLUE),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
    ]))
    story.append(bill_to)

    cust_info = (
        f"<b>{customer['name']}</b><br/>"
        f"{customer['address']}<br/>"
        f"Attn: {customer['contact']}<br/>"
        f"VAT Reg No: {customer['vat']}"
    )
    story.append(Paragraph(cust_info, _style("ci", fontSize=9, leftIndent=6,
                                              spaceBefore=3, spaceAfter=5)))

    # ── Line items ────────────────────────────────────────────────────────────
    rows = [[
        Paragraph("Description", S["bold"]),
        Paragraph("Qty", _style("qh", fontSize=9, fontName="Helvetica-Bold", alignment=TA_RIGHT)),
        Paragraph("Unit Price (excl. VAT)", S["bold_r"]),
        Paragraph("VAT (15%)", S["bold_r"]),
        Paragraph("Amount (incl. VAT)", S["bold_r"]),
    ]]

    subtotal_net = Decimal("0")
    subtotal_vat = Decimal("0")
    subtotal_gross = Decimal("0")

    for desc, qty, unit_net in lines:
        line_net   = (unit_net * qty).quantize(Decimal("0.01"), ROUND_HALF_UP)
        line_vat   = _vat(line_net)
        line_gross = line_net + line_vat
        subtotal_net   += line_net
        subtotal_vat   += line_vat
        subtotal_gross += line_gross
        rows.append([
            Paragraph(desc, S["body"]),
            Paragraph(str(qty), S["body_r"]),
            Paragraph(_fmt(unit_net), S["body_r"]),
            Paragraph(_fmt(line_vat), S["body_r"]),
            Paragraph(_fmt(line_gross), S["body_r"]),
        ])

    col_w = [W * 0.40, W * 0.07, W * 0.18, W * 0.15, W * 0.20]
    tbl = Table(rows, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0),  (-1, 0),  DARK_BLUE),
        ("TEXTCOLOR",     (0, 0),  (-1, 0),  colors.white),
        ("ROWBACKGROUNDS",(0, 1),  (-1, -1), [colors.white, ALT_ROW]),
        ("GRID",          (0, 0),  (-1, -1), 0.25, colors.lightgrey),
        ("FONTSIZE",      (0, 0),  (-1, -1), 9),
        ("TOPPADDING",    (0, 0),  (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0),  (-1, -1), 4),
        ("LEFTPADDING",   (0, 0),  (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0),  (-1, -1), 4),
        ("VALIGN",        (0, 0),  (-1, -1), "MIDDLE"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 4*mm))

    # ── Totals ────────────────────────────────────────────────────────────────
    totals = Table(
        [
            [Paragraph("Subtotal (excl. VAT):", S["body_r"]),  Paragraph(_fmt(subtotal_net),   S["body_r"])],
            [Paragraph("VAT @ 15%:",            S["body_r"]),  Paragraph(_fmt(subtotal_vat),   S["body_r"])],
            [Paragraph("TOTAL DUE (incl. VAT):",S["total"]),   Paragraph(_fmt(subtotal_gross), S["total"])],
        ],
        colWidths=[W * 0.75, W * 0.25],
    )
    totals.setStyle(TableStyle([
        ("LINEABOVE",  (0, 2), (-1, 2), 1.0, DARK_BLUE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(totals)
    story.append(Spacer(1, 6*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MID_BLUE))
    story.append(Spacer(1, 4*mm))

    # ── Banking details ───────────────────────────────────────────────────────
    banking = (
        f"<b>Banking Details — {OUR_BUSINESS['name']}</b><br/>"
        f"Bank: {OUR_BUSINESS['bank']}  |  "
        f"Account No: {OUR_BUSINESS['account']}  |  "
        f"Branch Code: {OUR_BUSINESS['branch']}  |  "
        f"Account Type: {OUR_BUSINESS['acc_type']}<br/>"
        f"Reference: <b>{invoice_number}</b>"
    )
    story.append(Paragraph(banking, _style("bank", fontSize=8, textColor=DARK_BLUE,
                                           borderPad=4, backColor=LIGHT_GREY)))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        "This is a computer-generated tax invoice. No signature is required. "
        "VAT Registration No: " + OUR_BUSINESS["vat"] + ".",
        _style("disc", fontSize=7, textColor=colors.grey, alignment=TA_CENTER),
    ))

    doc.build(story)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    out_dir = "sample_sales_invoices"
    os.makedirs(out_dir, exist_ok=True)

    categories = list(SERVICE_LINES.keys())
    counter = 0

    for cat in categories:
        items = SERVICE_LINES[cat]
        n_invoices = 75 // len(categories)

        for i in range(n_invoices):
            counter += 1
            inv_date  = _rand_date(2024)
            due_date  = inv_date + timedelta(days=30)
            customer  = random.choice(CUSTOMERS)

            # Pick 1–3 line items
            n_lines = random.randint(1, 3)
            selected = random.sample(items, min(n_lines, len(items)))
            lines = []
            for desc, base_price in selected:
                unit_net = Decimal(str(round(
                    base_price * random.uniform(0.9, 1.1), 2
                )))
                qty = random.randint(1, 3) if base_price < 10000 else 1
                lines.append((desc, qty, unit_net))

            inv_number = f"INV-{inv_date.year}-{counter:04d}"
            filename   = f"{counter:03d}_{cat}_{i+1:02d}.pdf"
            output     = os.path.join(out_dir, filename)

            _build_invoice_pdf(inv_number, inv_date, due_date, customer, lines, output)
            print(f"  [{counter:3d}] {filename}")

    print(f"\nDone — {counter} sales invoices written to ./{out_dir}/")


if __name__ == "__main__":
    print("Generating sales invoices...")
    main()
