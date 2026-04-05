"""
generate_receipts.py — Generate 100 realistic South African business receipts and
                       100 B2B tax invoices as PDFs.

Usage:
    pip install reportlab
    python generate_receipts.py

Output:
    ./sample_receipts/  — 100 retail-style receipts (fuel, supermarket, stationery,
                          utilities, restaurant)
    ./sample_invoices/  — 100 B2B tax invoices (professional services, IT, printing,
                          cleaning, security, advertising/marketing) with customer
                          details, payment terms, due dates, and banking details.
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
# DATA
# ---------------------------------------------------------------------------

VENDORS = {
    "fuel": [
        {
            "name": "Engen Petroleum (Pty) Ltd",
            "vat": "4010101010",
            "address": "12 Main Reef Road, Johannesburg, 2001",
            "phone": "011 555 1234",
            "doc_type": "Tax Invoice",
        },
        {
            "name": "BP Southern Africa (Pty) Ltd",
            "vat": "4020202020",
            "address": "45 William Nicol Drive, Sandton, 2196",
            "phone": "011 234 5678",
            "doc_type": "Tax Invoice",
        },
        {
            "name": "Total Energies Marketing SA (Pty) Ltd",
            "vat": "4030303030",
            "address": "88 Oxford Road, Rosebank, 2196",
            "phone": "011 345 6789",
            "doc_type": "Tax Invoice",
        },
        {
            "name": "Sasol Oil (Pty) Ltd — Caltex",
            "vat": "4040404040",
            "address": "14 Wierda Road West, Sandton, 2196",
            "phone": "011 456 7890",
            "doc_type": "Tax Invoice",
        },
    ],
    "supermarket": [
        {
            "name": "Checkers — Hyde Park Corner",
            "vat": "4050505050",
            "address": "Hyde Park Corner, Jan Smuts Ave, Johannesburg, 2196",
            "phone": "011 325 4500",
            "doc_type": "Receipt",
        },
        {
            "name": "Pick n Pay Retailers (Pty) Ltd",
            "vat": "4060606060",
            "address": "Eastgate Shopping Centre, Johannesburg, 2090",
            "phone": "011 615 7700",
            "doc_type": "Receipt",
        },
        {
            "name": "Woolworths (Pty) Ltd",
            "vat": "4070707070",
            "address": "Sandton City, 83 Rivonia Road, Sandton, 2196",
            "phone": "011 883 1100",
            "doc_type": "Tax Invoice",
        },
        {
            "name": "Spar Group Ltd — Greenstone",
            "vat": "4080808080",
            "address": "Greenstone Shopping Centre, Edenvale, 1609",
            "phone": "011 452 3300",
            "doc_type": "Receipt",
        },
    ],
    "stationery": [
        {
            "name": "Waltons Stationery Company (Pty) Ltd",
            "vat": "4090909090",
            "address": "22 Klipfontein Road, Rosettenville, Johannesburg, 2130",
            "phone": "011 688 5000",
            "doc_type": "Tax Invoice",
        },
        {
            "name": "CNA — Clearwater Mall",
            "vat": "4101010101",
            "address": "Clearwater Mall, Hendrik Potgieter Rd, Roodepoort, 1724",
            "phone": "011 675 4200",
            "doc_type": "Tax Invoice",
        },
        {
            "name": "Bidvest Stationery (Pty) Ltd",
            "vat": "4111111111",
            "address": "Alrode Industrial Park, Alberton, 1449",
            "phone": "011 900 6600",
            "doc_type": "Tax Invoice",
        },
        {
            "name": "PNA — Cresta Shopping Centre",
            "vat": "4121212121",
            "address": "Cresta Shopping Centre, Beyers Naude Drive, Johannesburg, 2194",
            "phone": "011 678 9900",
            "doc_type": "Receipt",
        },
    ],
    "utilities": [
        {
            "name": "Eskom Holdings SOC Ltd",
            "vat": "4131313131",
            "address": "Megawatt Park, Maxwell Drive, Sunninghill, 2157",
            "phone": "0860 037 566",
            "doc_type": "Tax Invoice",
        },
        {
            "name": "City of Johannesburg Metropolitan Municipality",
            "vat": "4141414141",
            "address": "68 Jorissen Street, Braamfontein, Johannesburg, 2001",
            "phone": "0860 562 874",
            "doc_type": "Tax Invoice",
        },
        {
            "name": "Telkom SA SOC Ltd",
            "vat": "4151515151",
            "address": "Telkom Towers North, 152 Proes Street, Pretoria, 0001",
            "phone": "10210",
            "doc_type": "Tax Invoice",
        },
        {
            "name": "Vodacom Business (Pty) Ltd",
            "vat": "4161616161",
            "address": "082 Vodacom Boulevard, Midrand, 1686",
            "phone": "082 111",
            "doc_type": "Tax Invoice",
        },
    ],
    "restaurant": [
        {
            "name": "Nando's Restaurants (Pty) Ltd",
            "vat": "4171717171",
            "address": "Rosebank Mall, 50 Bath Ave, Rosebank, 2196",
            "phone": "011 880 4400",
            "doc_type": "Receipt",
        },
        {
            "name": "Spur Corporation Ltd — Spur Steak Ranches",
            "vat": "4181818181",
            "address": "Tyger Valley Centre, Bellville, Cape Town, 7530",
            "phone": "021 555 5400",
            "doc_type": "Receipt",
        },
        {
            "name": "Ocean Basket (Pty) Ltd",
            "vat": "4191919191",
            "address": "Mall of Africa, Midrand, 1682",
            "phone": "011 312 8700",
            "doc_type": "Receipt",
        },
        {
            "name": "Steers (Famous Brands) (Pty) Ltd",
            "vat": "4202020202",
            "address": "Greenacres Shopping Centre, Port Elizabeth, 6001",
            "phone": "041 363 9800",
            "doc_type": "Receipt",
        },
        {
            "name": "Mugg & Bean (Famous Brands) (Pty) Ltd",
            "vat": "4212121212",
            "address": "Menlyn Park, Atterbury Road, Pretoria, 0181",
            "phone": "012 348 6600",
            "doc_type": "Receipt",
        },
    ],
}

LINE_ITEMS = {
    "fuel": [
        ("Unleaded 95 Petrol", 21.48, "litre"),
        ("Unleaded 93 Petrol", 20.89, "litre"),
        ("Diesel 50 ppm", 19.72, "litre"),
        ("Diesel 500 ppm", 19.45, "litre"),
        ("Premium 95 Petrol", 21.95, "litre"),
        ("Engine Oil 5W-30 (1L)", 189.00, "each"),
        ("Car Wash — Standard", 80.00, "each"),
        ("Screen Wash Fluid (500ml)", 45.00, "each"),
        ("Tyre Inflation (per tyre)", 15.00, "each"),
    ],
    "supermarket": [
        ("Clover Full Cream Milk 2L", 39.99, "each"),
        ("Albany Superior White Bread 700g", 18.99, "each"),
        ("Frisco Instant Coffee 250g", 89.99, "each"),
        ("Five Roses Teabags 100s", 64.99, "each"),
        ("Tastic Long Grain Rice 2kg", 54.99, "each"),
        ("Jungle Oats 1kg", 49.99, "each"),
        ("Coca-Cola 2L", 32.99, "each"),
        ("Sunlight Dishwashing Liquid 750ml", 27.99, "each"),
        ("Domestos Bleach 750ml", 34.99, "each"),
        ("Handy Andy Cream Cleaner 500ml", 29.99, "each"),
        ("Toilet Paper 9-pack", 79.99, "pack"),
        ("Pampers Active Baby Size 4 (52s)", 249.99, "pack"),
        ("Chicken Breast Fillet 1kg", 119.99, "kg"),
        ("Pork Rashers 500g", 89.99, "pack"),
        ("Gouda Cheese 400g", 79.99, "pack"),
        ("Golden Delicious Apples 1.5kg bag", 39.99, "bag"),
        ("Colgate Total Toothpaste 100ml", 44.99, "each"),
        ("Lux Body Wash 400ml", 49.99, "each"),
        ("Lucky Star Pilchards 400g", 29.99, "each"),
    ],
    "stationery": [
        ("A4 Copy Paper 500 sheets (Rotatrim)", 129.00, "ream"),
        ("A4 Copy Paper 5-ream box", 599.00, "box"),
        ("Black Ballpoint Pens (box of 50)", 149.00, "box"),
        ("Staedtler Marker Set (12 colours)", 189.00, "set"),
        ("Lever Arch File A4 (blue)", 69.00, "each"),
        ("Lever Arch File A4 (black)", 69.00, "each"),
        ("Box Files (set of 10)", 320.00, "set"),
        ("Scotch Tape 25mm x 33m", 39.00, "roll"),
        ("Sellotape Dispenser", 129.00, "each"),
        ("Stapler Standard", 149.00, "each"),
        ("Staples 26/6 (box of 5000)", 59.00, "box"),
        ("Scissors 200mm", 49.00, "each"),
        ("Correction Fluid (Tipp-Ex)", 29.00, "each"),
        ("Notebook A5 (ruled, 200pp)", 69.00, "each"),
        ("Notebook A4 (ruled, 192pp)", 89.00, "each"),
        ("Post-it Notes 76x76mm (100 sheets)", 79.00, "pad"),
        ("Printer Cartridge HP 650 Black", 349.00, "each"),
        ("Printer Cartridge HP 650 Colour", 399.00, "each"),
        ("Whiteboard Markers Set (4 colours)", 129.00, "set"),
        ("USB Flash Drive 32GB", 199.00, "each"),
    ],
    "utilities": [
        ("Electricity — Standard Tariff", None, "kWh"),
        ("Electricity — Block 1 (0–600 kWh)", 1.28, "kWh"),
        ("Electricity — Block 2 (>600 kWh)", 1.64, "kWh"),
        ("Water & Sanitation — Step 1", 12.39, "kl"),
        ("Water & Sanitation — Step 2", 18.62, "kl"),
        ("Refuse Removal — Business (monthly)", 890.00, "month"),
        ("Fixed Line Rental (ADSL)", 199.00, "month"),
        ("ADSL 10Mbps Uncapped", 699.00, "month"),
        ("Business Fibre 100Mbps", 1299.00, "month"),
        ("Mobile Data Bundle 10GB", 299.00, "bundle"),
        ("Business Voice Bundle 500 min", 499.00, "bundle"),
        ("Property Rates — Commercial", None, "month"),
        ("Sewerage Tariff", None, "month"),
    ],
    "restaurant": [
        ("1/4 Chicken & Chips", 109.90, "each"),
        ("1/2 Chicken & Chips", 149.90, "each"),
        ("Whole Chicken (PERi-PERi)", 239.90, "each"),
        ("Grilled Chicken Wrap", 99.90, "each"),
        ("Spur BBQ Platter (2 persons)", 459.90, "each"),
        ("Rump Steak 250g", 229.90, "each"),
        ("T-Bone Steak 400g", 329.90, "each"),
        ("Grilled Calamari", 179.90, "each"),
        ("Fish & Chips", 149.90, "each"),
        ("Seafood Platter (2 persons)", 499.90, "each"),
        ("Steers 6th Pounder Burger", 129.90, "each"),
        ("Steers Upgrade — Cheese", 15.00, "each"),
        ("Steers Upgrade — Bacon", 20.00, "each"),
        ("Chicken Burger", 89.90, "each"),
        ("Kids Meal — Burger & Chips", 79.90, "each"),
        ("Breakfast — Full English", 129.90, "each"),
        ("Cappuccino", 45.00, "each"),
        ("Americano", 35.00, "each"),
        ("Freshly Squeezed OJ", 55.00, "each"),
        ("Mineral Water 500ml", 25.00, "each"),
        ("Soft Drink (330ml can)", 29.90, "each"),
        ("Milkshake", 65.00, "each"),
        ("Cheesecake Slice", 79.90, "each"),
        ("Ice Cream Dessert", 55.00, "each"),
    ],
}

VAT_RATE = Decimal("0.15")

# ---------------------------------------------------------------------------
# B2B INVOICE DATA
# ---------------------------------------------------------------------------

INVOICE_VENDORS = {
    "professional_services": [
        {
            "name": "Grant Thornton Johannesburg (Pty) Ltd",
            "vat": "4310001001",
            "address": "150 Rivonia Road, Sandton, 2196",
            "phone": "011 322 4500",
            "email": "accounts@gtjhb.co.za",
            "bank": "ABSA Bank", "account": "4072819344", "branch": "632005", "acc_type": "Cheque",
        },
        {
            "name": "Bowmans Attorneys Inc.",
            "vat": "4310002002",
            "address": "165 West Street, Sandton, 2196",
            "phone": "011 669 9000",
            "email": "billing@bowmans.com",
            "bank": "First National Bank", "account": "6210093872", "branch": "250655", "acc_type": "Cheque",
        },
        {
            "name": "Deloitte & Touche (Pty) Ltd",
            "vat": "4310003003",
            "address": "The Woodlands, 20 Woodlands Drive, Woodmead, 2191",
            "phone": "011 806 5000",
            "email": "accounts@deloitte.co.za",
            "bank": "Standard Bank", "account": "0109882453", "branch": "051001", "acc_type": "Current",
        },
        {
            "name": "Nexia SAB&T (Pty) Ltd",
            "vat": "4310004004",
            "address": "119 Witch-Hazel Avenue, Centurion, 0157",
            "phone": "012 682 8800",
            "email": "invoices@nexiasabt.co.za",
            "bank": "Nedbank", "account": "1198004532", "branch": "198765", "acc_type": "Current",
        },
        {
            "name": "BDO South Africa Inc.",
            "vat": "4310005005",
            "address": "22 Wellington Road, Parktown, Johannesburg, 2193",
            "phone": "011 488 4000",
            "email": "fees@bdo.co.za",
            "bank": "ABSA Bank", "account": "4089123456", "branch": "632005", "acc_type": "Current",
        },
    ],
    "it_services": [
        {
            "name": "Dimension Data (Pty) Ltd",
            "vat": "4320001001",
            "address": "11 Diagonal Street, Johannesburg, 2001",
            "phone": "011 576 0000",
            "email": "invoicing@dimensiondata.com",
            "bank": "First National Bank", "account": "6290054321", "branch": "250655", "acc_type": "Cheque",
        },
        {
            "name": "BCX (Business Connexion) (Pty) Ltd",
            "vat": "4320002002",
            "address": "1090 Arrowhead Road, Randjespark, Midrand, 1685",
            "phone": "087 741 0000",
            "email": "accounts@bcx.co.za",
            "bank": "Standard Bank", "account": "0234567890", "branch": "051001", "acc_type": "Current",
        },
        {
            "name": "Liquid Intelligent Technologies SA (Pty) Ltd",
            "vat": "4320003003",
            "address": "Liquid House, Waterfall City, Midrand, 1682",
            "phone": "010 003 9999",
            "email": "billing@liquid.tech",
            "bank": "Nedbank", "account": "1987654321", "branch": "198765", "acc_type": "Current",
        },
        {
            "name": "Britehouse (Pty) Ltd",
            "vat": "4320004004",
            "address": "200 Janadel Avenue, Midrand, 1685",
            "phone": "011 028 5000",
            "email": "invoices@britehouse.co.za",
            "bank": "ABSA Bank", "account": "4056789012", "branch": "632005", "acc_type": "Cheque",
        },
    ],
    "printing": [
        {
            "name": "Paarl Media (Pty) Ltd",
            "vat": "4330001001",
            "address": "5 Dreyersdal Road, Bergvliet, Cape Town, 7945",
            "phone": "021 706 5300",
            "email": "accounts@paarlmedia.co.za",
            "bank": "First National Bank", "account": "6230011223", "branch": "250655", "acc_type": "Cheque",
        },
        {
            "name": "Minuteman Press Fourways",
            "vat": "4330002002",
            "address": "Fourways Mall, Johannesburg, 2191",
            "phone": "011 467 7788",
            "email": "billing@mmpress-fourways.co.za",
            "bank": "Capitec Business", "account": "1045678901", "branch": "470010", "acc_type": "Current",
        },
        {
            "name": "EGS Print Solutions (Pty) Ltd",
            "vat": "4330003003",
            "address": "17 Electron Avenue, Isando, Ekurhuleni, 1600",
            "phone": "011 392 5200",
            "email": "invoice@egsprints.co.za",
            "bank": "Standard Bank", "account": "0198765432", "branch": "051001", "acc_type": "Current",
        },
        {
            "name": "The Colour Factory (Pty) Ltd",
            "vat": "4330004004",
            "address": "23 Droste Circle, Randburg, 2196",
            "phone": "011 886 5411",
            "email": "accounts@colourfactory.co.za",
            "bank": "Nedbank", "account": "1176543210", "branch": "198765", "acc_type": "Cheque",
        },
    ],
    "cleaning": [
        {
            "name": "Supercare Facility Services (Pty) Ltd",
            "vat": "4340001001",
            "address": "96 Pretoria Street, Hillbrow, Johannesburg, 2001",
            "phone": "011 720 3000",
            "email": "invoicing@supercare.co.za",
            "bank": "ABSA Bank", "account": "4023456789", "branch": "632005", "acc_type": "Current",
        },
        {
            "name": "Bidvest Facilities Management (Pty) Ltd",
            "vat": "4340002002",
            "address": "Bidvest House, 18 Crescent Drive, Melrose Arch, 2196",
            "phone": "011 458 5000",
            "email": "facilities@bidvest.co.za",
            "bank": "First National Bank", "account": "6240022334", "branch": "250655", "acc_type": "Cheque",
        },
        {
            "name": "Cleanleaf Services (Pty) Ltd",
            "vat": "4340003003",
            "address": "45 Voortrekker Road, Bellville, Cape Town, 7530",
            "phone": "021 948 3300",
            "email": "billing@cleanleaf.co.za",
            "bank": "Capitec Business", "account": "1034567890", "branch": "470010", "acc_type": "Current",
        },
    ],
    "security": [
        {
            "name": "ADT Security (Pty) Ltd",
            "vat": "4350001001",
            "address": "ADT House, PO Box 2984, Halfway House, Midrand, 1685",
            "phone": "0860 111 900",
            "email": "accounts@adt.co.za",
            "bank": "Standard Bank", "account": "0254321098", "branch": "051001", "acc_type": "Current",
        },
        {
            "name": "Fidelity Security Services (Pty) Ltd",
            "vat": "4350002002",
            "address": "Fidelity House, 18 Electron Avenue, Isando, 1600",
            "phone": "0800 003 310",
            "email": "invoicing@fidelity-services.com",
            "bank": "ABSA Bank", "account": "4078901234", "branch": "632005", "acc_type": "Cheque",
        },
        {
            "name": "G4S Secure Solutions SA (Pty) Ltd",
            "vat": "4350003003",
            "address": "G4S House, 3 Protea Place, Sandton, 2196",
            "phone": "011 317 6700",
            "email": "billing@g4s.co.za",
            "bank": "Nedbank", "account": "1165432109", "branch": "198765", "acc_type": "Current",
        },
        {
            "name": "Securitas South Africa (Pty) Ltd",
            "vat": "4350004004",
            "address": "22 Skeen Boulevard, Bedfordview, Ekurhuleni, 2007",
            "phone": "011 455 3700",
            "email": "accounts@securitas.co.za",
            "bank": "First National Bank", "account": "6250033445", "branch": "250655", "acc_type": "Current",
        },
    ],
    "advertising": [
        {
            "name": "Ogilvy South Africa (Pty) Ltd",
            "vat": "4360001001",
            "address": "196 Buitenkant Street, Cape Town, 8001",
            "phone": "021 469 7000",
            "email": "finance@ogilvy.co.za",
            "bank": "Standard Bank", "account": "0298765432", "branch": "051001", "acc_type": "Cheque",
        },
        {
            "name": "TBWA\\Hunt\\Lascaris (Pty) Ltd",
            "vat": "4360002002",
            "address": "4 Biermann Avenue, Rosebank, Johannesburg, 2196",
            "phone": "011 709 7000",
            "email": "billing@tl.co.za",
            "bank": "ABSA Bank", "account": "4067890123", "branch": "632005", "acc_type": "Current",
        },
        {
            "name": "Webfluential (Pty) Ltd",
            "vat": "4360003003",
            "address": "11 Crescent Drive, Melrose Arch, Johannesburg, 2196",
            "phone": "011 026 3000",
            "email": "invoices@webfluential.com",
            "bank": "Capitec Business", "account": "1023456789", "branch": "470010", "acc_type": "Current",
        },
        {
            "name": "Native VML (Pty) Ltd",
            "vat": "4360004004",
            "address": "29 Baker Street, Rosebank, Johannesburg, 2196",
            "phone": "011 442 8940",
            "email": "accounts@nativevml.co.za",
            "bank": "First National Bank", "account": "6260044556", "branch": "250655", "acc_type": "Cheque",
        },
    ],
}

INVOICE_LINE_ITEMS = {
    "professional_services": [
        ("Audit & Assurance — Annual Financial Statements", 18500.00, "engagement"),
        ("Tax Advisory — Corporate Income Tax", 9500.00, "hour"),
        ("Legal Consultation — Contract Review", 4500.00, "hour"),
        ("Company Secretarial Services (monthly retainer)", 2800.00, "month"),
        ("CIPC Annual Return Filing", 1450.00, "each"),
        ("Payroll Administration (per employee)", 185.00, "employee"),
        ("HR Consulting — Policy Review", 6500.00, "day"),
        ("B-BBEE Compliance Advisory", 12000.00, "engagement"),
        ("Financial Due Diligence", 28000.00, "engagement"),
        ("Management Accounts Preparation (monthly)", 5500.00, "month"),
        ("VAT201 Return Submission", 1800.00, "return"),
        ("PAYE Reconciliation (bi-annual)", 3200.00, "submission"),
        ("Bookkeeping Services (monthly)", 4200.00, "month"),
        ("Business Valuation Report", 35000.00, "report"),
    ],
    "it_services": [
        ("Microsoft 365 Business Standard (per user/month)", 399.00, "user/month"),
        ("Microsoft Azure — Reserved Instance (monthly)", 8500.00, "month"),
        ("IT Support Retainer (monthly)", 6500.00, "month"),
        ("Network Infrastructure Maintenance", 12000.00, "month"),
        ("Cybersecurity Assessment", 28000.00, "engagement"),
        ("Firewall Licensing — FortiGate (annual)", 18500.00, "year"),
        ("Managed Backup Solution (monthly)", 2800.00, "month"),
        ("Software Development — Custom Module", 15000.00, "sprint"),
        ("Server Virtualisation (VMware license)", 45000.00, "license"),
        ("Wi-Fi Infrastructure Installation", 22000.00, "installation"),
        ("Help Desk Support (per incident)", 850.00, "incident"),
        ("Data Recovery Service", 9500.00, "engagement"),
        ("Domain & Hosting (annual)", 2400.00, "year"),
        ("SSL Certificate (2-year)", 1800.00, "certificate"),
        ("VoIP System Setup", 14500.00, "installation"),
    ],
    "printing": [
        ("Business Cards (500 — double-sided, full colour)", 895.00, "box"),
        ("A5 Brochures (1000 — full colour, gloss)", 3200.00, "run"),
        ("A4 Letterheads (500 — full colour)", 1850.00, "run"),
        ("Roll-up Banner (85x200cm)", 1450.00, "each"),
        ("Compliment Slips (500)", 750.00, "run"),
        ("Company Envelopes DL (500 — printed)", 980.00, "box"),
        ("A1 Poster (full colour, laminated)", 320.00, "each"),
        ("Notepads A5 (100 — 50 sheets, branded)", 2800.00, "run"),
        ("Flyers A4 (2000 — full colour)", 2200.00, "run"),
        ("Annual Report (50 copies — 40pp, perfect bound)", 18500.00, "run"),
        ("Promotional Stickers (1000 — die-cut)", 1200.00, "run"),
        ("Vinyl Signage — Vehicle Wrap", 8500.00, "vehicle"),
        ("Vinyl Signage — Shop Fascia (per m²)", 450.00, "m²"),
        ("Exhibition Backdrop (3m x 2m)", 5500.00, "each"),
    ],
    "cleaning": [
        ("Daily Office Cleaning — Standard (per day)", 850.00, "day"),
        ("Daily Office Cleaning — Premium (per day)", 1200.00, "day"),
        ("Monthly Cleaning Contract (up to 200m²)", 8500.00, "month"),
        ("Monthly Cleaning Contract (201–500m²)", 14500.00, "month"),
        ("Industrial Deep Clean", 18000.00, "engagement"),
        ("Carpet Shampooing (per m²)", 45.00, "m²"),
        ("Window Cleaning — External (per floor)", 1800.00, "floor"),
        ("Pest Control Treatment", 3500.00, "treatment"),
        ("Hygiene Services — Sanitary Bins (monthly)", 280.00, "unit/month"),
        ("Consumables Supply — Toilet Paper/Soap (monthly)", 1850.00, "month"),
        ("High-Pressure Cleaning — Parking Area", 5500.00, "engagement"),
    ],
    "security": [
        ("Armed Response — Monthly Fee", 1450.00, "month"),
        ("Monitoring Centre Fee (monthly)", 450.00, "month"),
        ("Guarding Services — 1 Guard (12-hour shift)", 1800.00, "shift"),
        ("CCTV Installation — 8 Camera System", 28000.00, "installation"),
        ("CCTV Maintenance Contract (monthly)", 1200.00, "month"),
        ("Access Control System Installation", 35000.00, "installation"),
        ("Electric Fence Installation (per metre)", 380.00, "metre"),
        ("Electric Fence Monitoring (monthly)", 650.00, "month"),
        ("Security Risk Assessment", 12000.00, "report"),
        ("Cash-in-Transit Service (per run)", 2800.00, "run"),
        ("Alarm System — Monitoring Only (monthly)", 350.00, "month"),
        ("Panic Button Device Rental (monthly)", 120.00, "month"),
    ],
    "advertising": [
        ("Digital Marketing Retainer (monthly)", 18500.00, "month"),
        ("Google Ads Management (monthly)", 8500.00, "month"),
        ("Google Ads Spend (media cost)", 25000.00, "month"),
        ("Facebook/Instagram Ads Management (monthly)", 6500.00, "month"),
        ("Social Media Content (monthly — 3 platforms)", 12000.00, "month"),
        ("SEO Optimisation (monthly)", 7500.00, "month"),
        ("Brand Identity Design — Full Package", 45000.00, "project"),
        ("Logo Design", 8500.00, "project"),
        ("Corporate Video Production (60 sec)", 85000.00, "project"),
        ("Photography — Corporate Shoot (half-day)", 12000.00, "half-day"),
        ("Email Marketing Campaign (design + send)", 4500.00, "campaign"),
        ("Website Design & Development", 65000.00, "project"),
        ("Website Maintenance (monthly)", 2500.00, "month"),
        ("Influencer Campaign Management", 22000.00, "campaign"),
        ("Media Planning & Buying", 15000.00, "month"),
    ],
}

# Fake SA SME customers for "Bill To" block
SME_CUSTOMERS = [
    {"name": "Nkosi Logistics (Pty) Ltd", "vat": "4500100001", "address": "14 Industrial Road, Germiston, 1401", "contact": "Sipho Nkosi"},
    {"name": "Dlamini & Associates CC", "vat": "4500200002", "address": "22 Church Street, Pretoria, 0001", "contact": "Thandi Dlamini"},
    {"name": "Cape Coast Trading (Pty) Ltd", "vat": "4500300003", "address": "55 Adderley Street, Cape Town, 8001", "contact": "Johan van der Merwe"},
    {"name": "Sunrise Catering Solutions CC", "vat": "4500400004", "address": "8 Market Square, Durban, 4001", "contact": "Priya Pillay"},
    {"name": "Ubuntu Tech Ventures (Pty) Ltd", "vat": "4500500005", "address": "3 Innovation Hub Drive, Tshwane, 0087", "contact": "Lungelo Zulu"},
    {"name": "Boland Agricultural Supplies CC", "vat": "4500600006", "address": "12 Voortrekker Street, Paarl, 7646", "contact": "Kobus Mostert"},
    {"name": "Ndlovu Consulting Group (Pty) Ltd", "vat": "4500700007", "address": "Office Park, 15 Alice Lane, Sandton, 2196", "contact": "Bongiwe Ndlovu"},
    {"name": "SA Steel Fabricators CC", "vat": "4500800008", "address": "Plot 44, Alrode South, Alberton, 1449", "contact": "Gerhard Pretorius"},
    {"name": "Coastal Pharmacy Group (Pty) Ltd", "vat": "4500900009", "address": "88 Marine Drive, Bloubergstrand, 7441", "contact": "Fatima Ebrahim"},
    {"name": "Highveld Electrical Contractors CC", "vat": "4501000010", "address": "19 Power Street, Witbank, 1034", "contact": "Nico Kruger"},
    {"name": "Jozi Fresh Produce (Pty) Ltd", "vat": "4501100011", "address": "Johannesburg Fresh Produce Market, Selby, 2001", "contact": "Emmanuel Okonkwo"},
    {"name": "Table Mountain Tourism Services CC", "vat": "4501200012", "address": "Lower Tafelberg Road, Cape Town, 8001", "contact": "Anele Mkhize"},
    {"name": "Gauteng Office Interiors (Pty) Ltd", "vat": "4501300013", "address": "34 Electron Avenue, Isando, 1600", "contact": "Liezel Fourie"},
    {"name": "Kwa-Zulu Textile Merchants CC", "vat": "4501400014", "address": "Grey Street Commercial Centre, Durban, 4001", "contact": "Rajan Govender"},
    {"name": "Platinum Valley Properties (Pty) Ltd", "vat": "4501500015", "address": "Suite 4, Rustenburg Office Park, 0299", "contact": "Busisiwe Mahlangu"},
]

PAYMENT_TERMS = [
    ("Immediate", 0),
    ("Net 7", 7),
    ("Net 14", 14),
    ("Net 30", 30),
    ("Net 60", 60),
    ("EOM", 30),   # End of month ≈ 30 days
]


def rand_date(start_year=2024, end_year=2025):
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 12, 31)
    delta = end - start
    return start + timedelta(days=random.randint(0, delta.days))


def invoice_number(category, index):
    prefixes = {
        "fuel": "FU", "supermarket": "SM", "stationery": "ST",
        "utilities": "UT", "restaurant": "RE",
        "professional_services": "PS", "it_services": "IT",
        "printing": "PR", "cleaning": "CL", "security": "SE", "advertising": "AD",
    }
    prefix = prefixes.get(category, "XX")
    return f"{prefix}-{random.randint(10000,99999)}-{index:04d}"


def po_number():
    return f"PO-{random.randint(1000, 9999)}"


def format_zar(amount: Decimal) -> str:
    return f"R {amount:,.2f}"


def pick_line_items(category: str):
    pool = LINE_ITEMS[category]
    n = random.randint(2, 6)
    chosen = random.sample(pool, min(n, len(pool)))
    items = []
    for name, unit_price, unit in chosen:
        if unit in ("litre",):
            qty = Decimal(str(round(random.uniform(20, 65), 2)))
        elif unit in ("kWh",):
            qty = Decimal(str(random.randint(200, 2500)))
            if unit_price is None:
                unit_price = round(random.uniform(1.20, 2.10), 4)
        elif unit in ("kl",):
            qty = Decimal(str(round(random.uniform(5, 40), 1)))
        elif unit in ("month",):
            qty = Decimal("1")
            if unit_price is None:
                unit_price = round(random.uniform(400, 2500), 2)
        else:
            qty = Decimal(str(random.randint(1, 5)))
        unit_price = Decimal(str(unit_price))
        line_total = (qty * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        items.append({
            "description": name,
            "qty": qty,
            "unit": unit,
            "unit_price": unit_price,
            "total": line_total,
        })
    return items


def pick_invoice_line_items(category: str):
    pool = INVOICE_LINE_ITEMS[category]
    n = random.randint(2, 5)
    chosen = random.sample(pool, min(n, len(pool)))
    items = []
    for name, unit_price, unit in chosen:
        # Units that make sense to multiply
        if unit in ("hour", "day", "half-day", "shift", "user/month", "employee",
                    "unit/month", "metre", "m²", "run"):
            qty = Decimal(str(random.randint(1, 12)))
        else:
            qty = Decimal("1")
        unit_price = Decimal(str(unit_price))
        line_total = (qty * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        items.append({
            "description": name,
            "qty": qty,
            "unit": unit,
            "unit_price": unit_price,
            "total": line_total,
        })
    return items


def build_pdf(filepath: str, vendor: dict, category: str, doc_type: str, index: int):
    date = rand_date()
    inv_no = invoice_number(category, index)
    items = pick_line_items(category)

    subtotal = sum(i["total"] for i in items)
    subtotal = subtotal.quantize(Decimal("0.01"))
    vat_amount = (subtotal * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = (subtotal + vat_amount).quantize(Decimal("0.01"))

    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    normal.fontName = "Helvetica"
    normal.fontSize = 9
    normal.leading = 13

    title_style = ParagraphStyle(
        "title",
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        alignment=TA_LEFT,
    )
    heading2 = ParagraphStyle(
        "heading2",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        alignment=TA_LEFT,
    )
    small = ParagraphStyle(
        "small",
        fontName="Helvetica",
        fontSize=8,
        leading=11,
    )
    right_style = ParagraphStyle(
        "right",
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        alignment=TA_RIGHT,
    )
    right_bold = ParagraphStyle(
        "right_bold",
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=14,
        alignment=TA_RIGHT,
    )

    story = []

    # Header — vendor block (left) + doc type/number (right)
    header_data = [
        [
            Paragraph(vendor["name"], title_style),
            Paragraph(f"<b>{doc_type.upper()}</b>", ParagraphStyle(
                "doc_type", fontName="Helvetica-Bold", fontSize=14, alignment=TA_RIGHT
            )),
        ],
        [
            Paragraph(vendor["address"], small),
            Paragraph(
                f"<b>No:</b> {inv_no}<br/>"
                f"<b>Date:</b> {date.strftime('%d %B %Y')}<br/>"
                f"<b>VAT Reg No:</b> {vendor['vat']}<br/>"
                f"<b>Tel:</b> {vendor['phone']}",
                ParagraphStyle("right_small", fontName="Helvetica", fontSize=8, alignment=TA_RIGHT, leading=12),
            ),
        ],
    ]
    header_table = Table(header_data, colWidths=[105 * mm, 65 * mm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1a1a2e"), spaceAfter=8))

    # Customer / Bill To block
    story.append(Paragraph("Bill To:", heading2))
    story.append(Paragraph(
        "Business Client<br/>VAT Reg No: (on file)<br/>Account: CASH / EFT",
        ParagraphStyle("bill_to", fontName="Helvetica", fontSize=9, leading=13, leftIndent=5 * mm),
    ))
    story.append(Spacer(1, 8 * mm))

    # Line items table
    table_header = ["#", "Description", "Qty", "Unit", "Unit Price (excl.)", "Amount (excl.)"]
    table_data = [table_header]

    for idx, item in enumerate(items, 1):
        table_data.append([
            str(idx),
            item["description"],
            f"{item['qty']:g}",
            item["unit"],
            format_zar(item["unit_price"]),
            format_zar(item["total"]),
        ])

    line_table = Table(
        table_data,
        colWidths=[8 * mm, 72 * mm, 16 * mm, 14 * mm, 28 * mm, 32 * mm],
        repeatRows=1,
    )
    line_table.setStyle(TableStyle([
        # Header row
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        # Body rows
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("ALIGN", (0, 1), (0, -1), "CENTER"),
        ("ALIGN", (2, 1), (5, -1), "RIGHT"),
        ("ALIGN", (3, 1), (3, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        # Alternating rows
        *[
            ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f5f5f5") if i % 2 == 0 else colors.white)
            for i in range(1, len(table_data))
        ],
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#1a1a2e")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(line_table)
    story.append(Spacer(1, 4 * mm))

    # Totals block (right-aligned)
    totals_data = [
        ["Subtotal (excl. VAT):", format_zar(subtotal)],
        [f"VAT (15%):", format_zar(vat_amount)],
        ["TOTAL (incl. VAT):", format_zar(total)],
    ]
    totals_table = Table(totals_data, colWidths=[50 * mm, 30 * mm], hAlign="RIGHT")
    totals_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 1), "Helvetica"),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 1), 9),
        ("FONTSIZE", (0, 2), (-1, 2), 10),
        ("ALIGN", (0, 0), (0, -1), "RIGHT"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, 2), (-1, 2), 1, colors.HexColor("#1a1a2e")),
        ("LINEBELOW", (0, 2), (-1, 2), 1, colors.HexColor("#1a1a2e")),
        ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#e8f4e8")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (1, 0), (1, -1), 0),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 8 * mm))

    # Footer / payment info
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#999999"), spaceAfter=4))
    story.append(Paragraph(
        "<b>This is a valid South African VAT invoice. VAT is charged at 15% in terms of the Value-Added Tax Act, 89 of 1991.</b>",
        ParagraphStyle("footer_bold", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=colors.HexColor("#444444")),
    ))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        f"Please retain this {doc_type.lower()} for your SARS records. "
        "Documents must be retained for a minimum of 5 years in terms of the Tax Administration Act.",
        ParagraphStyle("footer", fontName="Helvetica", fontSize=7, leading=10, textColor=colors.HexColor("#666666")),
    ))

    doc.build(story)


def build_invoice_pdf(filepath: str, vendor: dict, category: str, index: int):
    invoice_date = rand_date()
    inv_no = invoice_number(category, index)
    po_ref = po_number()
    customer = random.choice(SME_CUSTOMERS)
    terms_label, terms_days = random.choice(PAYMENT_TERMS)
    due_date = invoice_date + timedelta(days=terms_days)
    items = pick_invoice_line_items(category)

    subtotal = sum(i["total"] for i in items).quantize(Decimal("0.01"))
    vat_amount = (subtotal * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = (subtotal + vat_amount).quantize(Decimal("0.01"))

    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    BRAND = colors.HexColor("#0d3b6e")   # dark navy — professional feel
    BRAND_LIGHT = colors.HexColor("#e8f0fb")

    def ps(name, **kwargs):
        base = dict(fontName="Helvetica", fontSize=9, leading=13)
        base.update(kwargs)
        return ParagraphStyle(name, **base)

    h1 = ps("h1", fontName="Helvetica-Bold", fontSize=18, leading=22)
    h2 = ps("h2", fontName="Helvetica-Bold", fontSize=10, leading=13)
    small = ps("small", fontSize=8, leading=11)
    small_r = ps("small_r", fontSize=8, leading=11, alignment=TA_RIGHT)
    right_bold = ps("right_bold", fontName="Helvetica-Bold", fontSize=10,
                    leading=13, alignment=TA_RIGHT)

    story = []

    # ── Header bar ──────────────────────────────────────────────────────────
    header_data = [
        [
            Paragraph(vendor["name"], h1),
            Paragraph("TAX INVOICE", ps("inv_label", fontName="Helvetica-Bold",
                                         fontSize=20, leading=24,
                                         textColor=BRAND, alignment=TA_RIGHT)),
        ],
        [
            Paragraph(
                f"{vendor['address']}<br/>"
                f"Tel: {vendor['phone']} &nbsp;|&nbsp; {vendor['email']}<br/>"
                f"VAT Reg No: {vendor['vat']}",
                small,
            ),
            Paragraph(
                f"<b>Invoice No:</b> {inv_no}<br/>"
                f"<b>Invoice Date:</b> {invoice_date.strftime('%d %B %Y')}<br/>"
                f"<b>Due Date:</b> {due_date.strftime('%d %B %Y')}<br/>"
                f"<b>Payment Terms:</b> {terms_label}",
                small_r,
            ),
        ],
    ]
    ht = Table(header_data, colWidths=[110 * mm, 60 * mm])
    ht.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(ht)
    story.append(HRFlowable(width="100%", thickness=2, color=BRAND, spaceAfter=6))

    # ── Bill To / Ship To / PO ref ──────────────────────────────────────────
    bill_data = [
        [
            Paragraph("<b>Bill To</b>", h2),
            Paragraph("<b>Purchase Order</b>", h2),
        ],
        [
            Paragraph(
                f"{customer['name']}<br/>"
                f"Attn: {customer['contact']}<br/>"
                f"{customer['address']}<br/>"
                f"VAT Reg No: {customer['vat']}",
                ps("bill_val", fontSize=8, leading=12),
            ),
            Paragraph(
                f"<b>PO Number:</b> {po_ref}<br/>"
                f"<b>Account Ref:</b> {customer['name'][:20]}",
                ps("po_val", fontSize=8, leading=12),
            ),
        ],
    ]
    bt = Table(bill_data, colWidths=[110 * mm, 60 * mm])
    bt.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_LIGHT),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(bt)
    story.append(Spacer(1, 6 * mm))

    # ── Line items ──────────────────────────────────────────────────────────
    col_headers = ["#", "Description", "Qty", "Unit", "Unit Price (excl. VAT)", "Line Total (excl. VAT)"]
    table_data = [col_headers]
    for idx, item in enumerate(items, 1):
        table_data.append([
            str(idx),
            item["description"],
            f"{item['qty']:g}",
            item["unit"],
            format_zar(item["unit_price"]),
            format_zar(item["total"]),
        ])

    lt = Table(
        table_data,
        colWidths=[8 * mm, 74 * mm, 14 * mm, 18 * mm, 28 * mm, 28 * mm],
        repeatRows=1,
    )
    lt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("ALIGN", (0, 1), (0, -1), "CENTER"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("ALIGN", (3, 1), (3, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BRAND_LIGHT]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, BRAND),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(lt)
    story.append(Spacer(1, 4 * mm))

    # ── Totals (right) + Banking details (left) ─────────────────────────────
    totals_block = [
        ["Subtotal (excl. VAT):", format_zar(subtotal)],
        ["VAT @ 15%:", format_zar(vat_amount)],
        ["AMOUNT DUE (incl. VAT):", format_zar(total)],
    ]
    totals_tbl = Table(totals_block, colWidths=[55 * mm, 32 * mm])
    totals_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 1), "Helvetica"),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 1), 9),
        ("FONTSIZE", (0, 2), (-1, 2), 10),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("LINEABOVE", (0, 2), (-1, 2), 1.5, BRAND),
        ("LINEBELOW", (0, 2), (-1, 2), 1.5, BRAND),
        ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#d0e8ff")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (1, 0), (1, -1), 0),
    ]))

    banking_text = (
        f"<b>EFT Banking Details</b><br/>"
        f"Bank: {vendor['bank']}<br/>"
        f"Account No: {vendor['account']}<br/>"
        f"Branch Code: {vendor['branch']}<br/>"
        f"Account Type: {vendor['acc_type']}<br/>"
        f"<b>Reference: {inv_no}</b>"
    )
    banking_para = Paragraph(banking_text, ps("bank", fontSize=8, leading=12))

    side_by_side = Table(
        [[banking_para, totals_tbl]],
        colWidths=[95 * mm, 75 * mm],
    )
    side_by_side.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("LINEABOVE", (0, 0), (0, 0), 0.5, colors.HexColor("#999999")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(side_by_side)
    story.append(Spacer(1, 6 * mm))

    # ── Footer ───────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#aaaaaa"), spaceAfter=4))
    story.append(Paragraph(
        "This is a valid tax invoice in terms of the Value-Added Tax Act, 89 of 1991. "
        "VAT is levied at 15%. Please quote the invoice number on all correspondence and EFT payments.",
        ps("foot1", fontSize=7, leading=10, textColor=colors.HexColor("#555555")),
    ))
    story.append(Spacer(1, 1 * mm))
    story.append(Paragraph(
        f"Interest at prime + 2% per annum will be charged on overdue accounts. "
        "Documents must be retained for 5 years per the Tax Administration Act, 28 of 2011.",
        ps("foot2", fontSize=7, leading=10, textColor=colors.HexColor("#777777")),
    ))

    doc.build(story)


def main():
    base = os.path.dirname(__file__)

    # ── 100 receipts ──────────────────────────────────────────────────────
    receipts_dir = os.path.join(base, "sample_receipts")
    os.makedirs(receipts_dir, exist_ok=True)

    receipt_dist = {
        "fuel": 18,
        "supermarket": 22,
        "stationery": 20,
        "utilities": 15,
        "restaurant": 25,
    }
    assert sum(receipt_dist.values()) == 100

    count = 0
    for category, n in receipt_dist.items():
        for i in range(1, n + 1):
            count += 1
            vendor = random.choice(VENDORS[category])
            filename = f"{count:03d}_{category}_{vendor['name'].split()[0].lower()}_{i:02d}.pdf"
            build_pdf(os.path.join(receipts_dir, filename), vendor, category, vendor["doc_type"], count)
            print(f"[receipt {count:3d}/100] {filename}")

    print(f"\n100 receipts saved to: {receipts_dir}\n")

    # ── 100 B2B invoices ──────────────────────────────────────────────────
    invoices_dir = os.path.join(base, "sample_invoices")
    os.makedirs(invoices_dir, exist_ok=True)

    invoice_dist = {
        "professional_services": 20,
        "it_services": 18,
        "printing": 15,
        "cleaning": 15,
        "security": 15,
        "advertising": 17,
    }
    assert sum(invoice_dist.values()) == 100

    count = 0
    for category, n in invoice_dist.items():
        for i in range(1, n + 1):
            count += 1
            vendor = random.choice(INVOICE_VENDORS[category])
            safe = vendor['name'].split()[0].lower()
            safe = "".join(c if c.isalnum() or c == "_" else "" for c in safe)
            filename = f"{count:03d}_{category}_{safe}_{i:02d}.pdf"
            build_invoice_pdf(os.path.join(invoices_dir, filename), vendor, category, count)
            print(f"[invoice  {count:3d}/100] {filename}")

    print(f"\n100 invoices saved to: {invoices_dir}")


if __name__ == "__main__":
    main()
