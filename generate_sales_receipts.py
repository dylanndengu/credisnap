"""
generate_sales_receipts.py — Generate 50 realistic South African sales receipts
                              issued BY Baba TO customers, representing income.

Usage:
    python generate_sales_receipts.py

Output:
    ./sample_sales_receipts/  — 50 till-style receipts issued by Baba (the seller).
"""

import os
import random
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
)
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

# ---------------------------------------------------------------------------
# OUR BUSINESS — the seller (must match DB business_name)
# ---------------------------------------------------------------------------

OUR_BUSINESS = {
    "name":    "Baba",
    "vat":     "4500001234",
    "address": "14 Commerce Street, Johannesburg, 2001",
    "phone":   "011 123 4567",
    "email":   "info@baba.co.za",
}

# ---------------------------------------------------------------------------
# CUSTOMERS
# ---------------------------------------------------------------------------

CUSTOMERS = [
    "Sipho Dlamini",
    "Thandi Nkosi",
    "Kefilwe Motsepe",
    "Lungelo Zulu",
    "Amahle Buthelezi",
    "Lebo Mokoena",
    "Zanele Khumalo",
    "Mpho Sithole",
    "Nomsa Mthembu",
    "Bongani Shabalala",
    "Siya Ntuli",
    "Naledi Dube",
    "Mandla Cele",
    "Ayanda Mkhize",
    "Thandeka Mhlongo",
    "Cash Customer",
    "Walk-in Customer",
]

# ---------------------------------------------------------------------------
# SERVICES / GOODS SOLD
# ---------------------------------------------------------------------------

SALE_LINES = [
    ("Consulting Services (hourly rate)",        850.00),
    ("Business Advisory Session",               1200.00),
    ("Project Planning Workshop (half day)",    2500.00),
    ("Market Research Report",                  3800.00),
    ("Financial Analysis — Standard",           1800.00),
    ("Administrative Support (monthly)",        4500.00),
    ("Data Capture Services",                    650.00),
    ("Document Preparation & Review",            980.00),
    ("Training Session — 2 hrs",               1500.00),
    ("Coaching Session (1 hr)",                  750.00),
    ("Event Coordination (per day)",            3200.00),
    ("Social Media Management (monthly)",       2800.00),
    ("Content Writing — per article",            450.00),
    ("Translation Services (per page)",          350.00),
    ("Logistics Coordination Fee",              1100.00),
    ("Procurement Assistance",                  1600.00),
    ("Supplier Liaison Services",               1350.00),
    ("Quality Assurance Review",                2200.00),
    ("Compliance Check — Standard",             1750.00),
    ("Report Writing (standard)",               1900.00),
]

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
# PDF builder — till-receipt style
# ---------------------------------------------------------------------------

BASE_STYLES = getSampleStyleSheet()


def _style(name, **kw):
    return ParagraphStyle(name, parent=BASE_STYLES["Normal"], **kw)


DARK  = colors.HexColor("#1A1A2E")
ACCENT = colors.HexColor("#16213E")
LIGHT = colors.HexColor("#F5F5F5")


def _build_receipt_pdf(
    receipt_number: str,
    receipt_date: datetime,
    customer: str,
    lines: list[tuple[str, int, Decimal]],
    output_path: str,
) -> None:
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=15*mm, bottomMargin=15*mm,
    )
    W = A4[0] - 40*mm

    S = {
        "biz":     _style("biz", fontSize=16, fontName="Helvetica-Bold",
                          textColor=DARK, alignment=TA_CENTER),
        "sub":     _style("sub", fontSize=8, textColor=colors.grey, alignment=TA_CENTER),
        "label":   _style("lbl", fontSize=8, textColor=colors.grey),
        "body":    _style("bdy", fontSize=9),
        "body_r":  _style("bdr", fontSize=9, alignment=TA_RIGHT),
        "bold":    _style("bld", fontSize=9, fontName="Helvetica-Bold"),
        "bold_r":  _style("blr", fontSize=9, fontName="Helvetica-Bold", alignment=TA_RIGHT),
        "total":   _style("tot", fontSize=11, fontName="Helvetica-Bold",
                          textColor=DARK, alignment=TA_RIGHT),
        "footer":  _style("ftr", fontSize=7, textColor=colors.grey, alignment=TA_CENTER),
    }

    story = []

    # ── Header ──────────────────────────────────────────────────────────────
    story.append(Paragraph(OUR_BUSINESS["name"], S["biz"]))
    story.append(Paragraph(OUR_BUSINESS["address"], S["sub"]))
    story.append(Paragraph(
        f"Tel: {OUR_BUSINESS['phone']}  |  {OUR_BUSINESS['email']}", S["sub"]
    ))
    story.append(Paragraph(f"VAT Reg No: {OUR_BUSINESS['vat']}", S["sub"]))
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=DARK))
    story.append(Spacer(1, 3*mm))

    # ── Receipt meta ─────────────────────────────────────────────────────────
    meta = Table(
        [
            [Paragraph("RECEIPT", _style("rt", fontSize=13, fontName="Helvetica-Bold",
                                          textColor=ACCENT)), ""],
            [Paragraph(f"Receipt No:", S["label"]),
             Paragraph(receipt_number, S["body_r"])],
            [Paragraph("Date:", S["label"]),
             Paragraph(receipt_date.strftime("%d %B %Y  %H:%M"), S["body_r"])],
            [Paragraph("Served:", S["label"]),
             Paragraph(customer, S["body_r"])],
        ],
        colWidths=[W * 0.5, W * 0.5],
    )
    meta.setStyle(TableStyle([
        ("SPAN",          (0, 0), (-1, 0)),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(meta)
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))

    # ── Line items ────────────────────────────────────────────────────────────
    rows = [[
        Paragraph("Item", S["bold"]),
        Paragraph("Qty", S["bold_r"]),
        Paragraph("Unit (excl.)", S["bold_r"]),
        Paragraph("VAT", S["bold_r"]),
        Paragraph("Total", S["bold_r"]),
    ]]

    subtotal_net   = Decimal("0")
    subtotal_vat   = Decimal("0")
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

    col_w = [W * 0.38, W * 0.07, W * 0.20, W * 0.15, W * 0.20]
    tbl = Table(rows, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0),  (-1, 0),  DARK),
        ("TEXTCOLOR",     (0, 0),  (-1, 0),  colors.white),
        ("ROWBACKGROUNDS",(0, 1),  (-1, -1), [colors.white, LIGHT]),
        ("GRID",          (0, 0),  (-1, -1), 0.25, colors.lightgrey),
        ("FONTSIZE",      (0, 0),  (-1, -1), 8),
        ("TOPPADDING",    (0, 0),  (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0),  (-1, -1), 3),
        ("LEFTPADDING",   (0, 0),  (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0),  (-1, -1), 3),
        ("VALIGN",        (0, 0),  (-1, -1), "MIDDLE"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 3*mm))

    # ── Totals ────────────────────────────────────────────────────────────────
    totals = Table(
        [
            [Paragraph("Subtotal (excl. VAT):", S["body_r"]),
             Paragraph(_fmt(subtotal_net),   S["body_r"])],
            [Paragraph("VAT @ 15%:",          S["body_r"]),
             Paragraph(_fmt(subtotal_vat),   S["body_r"])],
            [Paragraph("TOTAL:",              S["total"]),
             Paragraph(_fmt(subtotal_gross), S["total"])],
        ],
        colWidths=[W * 0.75, W * 0.25],
    )
    totals.setStyle(TableStyle([
        ("LINEABOVE",     (0, 2), (-1, 2), 1.0, DARK),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(totals)
    story.append(Spacer(1, 6*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
    story.append(Spacer(1, 3*mm))

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Paragraph("Thank you for your business!", S["footer"]))
    story.append(Paragraph(
        f"This is a tax receipt issued by {OUR_BUSINESS['name']}. "
        f"VAT Reg No: {OUR_BUSINESS['vat']}.",
        S["footer"],
    ))

    doc.build(story)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    out_dir = "sample_sales_receipts"
    os.makedirs(out_dir, exist_ok=True)

    for i in range(1, 101):
        receipt_date = _rand_date(2024)
        receipt_date = receipt_date.replace(
            hour=random.randint(8, 17),
            minute=random.randint(0, 59),
        )
        customer = random.choice(CUSTOMERS)

        n_lines = random.randint(1, 3)
        selected = random.sample(SALE_LINES, n_lines)
        lines = []
        for desc, base_price in selected:
            unit_net = Decimal(str(round(base_price * random.uniform(0.9, 1.1), 2)))
            qty = random.randint(1, 3) if base_price < 1000 else 1
            lines.append((desc, qty, unit_net))

        receipt_number = f"REC-{receipt_date.year}-{i:04d}"
        filename = f"{i:03d}_sales_receipt.pdf"
        output = os.path.join(out_dir, filename)

        _build_receipt_pdf(receipt_number, receipt_date, customer, lines, output)
        print(f"  [{i:3d}] {filename}")

    print(f"\nDone — 100 sales receipts written to ./{out_dir}/")


if __name__ == "__main__":
    print("Generating sales receipts for Baba...")
    main()
