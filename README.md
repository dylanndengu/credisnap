# CrediSnap

WhatsApp-based financial statement generator for South African SMEs.
Users upload receipts and invoices via WhatsApp; CrediSnap extracts the data, categorises it, and produces IFRS-aligned financial reports for loan applications.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Interface | WhatsApp Business API (Twilio) |
| Backend | Python — FastAPI |
| Database | PostgreSQL (Supabase) + AWS S3 (document storage) |
| AI / OCR | AWS Textract (extraction) + Claude (classification & categorisation) |

## Compliance

- **POPIA** (Act 4 of 2013) — consent tracking, data retention, soft-delete support
- **SARS** — 5-year record-keeping (TAA s29), VAT at 15%, bi-monthly VAT periods
- **IFRS for SMEs** — double-entry ledger, P&L and Balance Sheet aligned to IFRS line items
- **Currency** — ZAR throughout

---

## WhatsApp Command Reference

| Command | Description |
|---|---|
| Send photo / PDF | Record a receipt or invoice |
| `CASH SALE` | Record a cash sale with no receipt (guided text flow) |
| `YES` | Confirm and save a pending entry |
| `NO` | Something's wrong → opens structured correction menu |
| `EDIT` | Correct the most recently recorded entry |
| `BALANCE` | Income vs expenses snapshot for the current month |
| `LAST` | Details of the most recent journal entry |
| `TOP` | Top 5 spending categories this month |
| `PENDING` | List all receipts awaiting confirmation |
| `REPORT` | Generate a full financial PDF (prompts for year) |
| `REPORT 2025` | Generate a full financial PDF for a specific year |

---

## Build Progress

### Step 1 — Database Schema ✅
*Files: [`db/migrations/001_initial_schema.sql`](db/migrations/001_initial_schema.sql), [`db/migrations/002_seed_chart_of_accounts.sql`](db/migrations/002_seed_chart_of_accounts.sql)*

PostgreSQL schema covering the full data model:

- **`users`** — SME business accounts identified by WhatsApp number. Includes CIPC registration, SARS VAT/tax references, POPIA consent fields, and a configurable financial year-end month.
- **`accounts`** — Chart of Accounts per user, seeded from a standard SA SME template (`account_templates`). Hierarchical (self-referencing `parent_id`), with an `ifrs_line_item` column that drives automated financial statement grouping.
- **`documents`** — Every file uploaded via WhatsApp. Stores the S3 location, raw OCR JSON (immutable audit trail), structured extraction output, and a `PENDING → PROCESSING → EXTRACTED → POSTED` status lifecycle.
- **`journal_entries`** — One row per financial event. Links back to its source document. AI-generated entries start as `DRAFT`; a confidence threshold either auto-posts them or sends a WhatsApp confirmation to the user.
- **`journal_entry_lines`** — The double-entry core. Each line carries a debit or credit amount (never both). A DB-level trigger enforces that debits = credits before any entry can transition to `POSTED`. A second trigger makes posted entries immutable — corrections require reversing entries.
- **`v_account_balances`** — View that aggregates posted lines by account and calendar month. Financial statement queries are a simple `GROUP BY ifrs_line_item` against this view.
- **`vat_entries`** ([`003_vat_entries.sql`](db/migrations/003_vat_entries.sql)) — One row per VATable transaction. Links gross/net/VAT amounts, VAT code (SR/ZR/EX/OP), supplier VAT number, and tax period back to the journal entry. The `v_vat201_summary` view aggregates these per bi-monthly period for the SARS VAT201 return.

The standard SA SME Chart of Accounts covers account codes 1000–6999 (Assets → Liabilities → Equity → Revenue → Cost of Sales → Operating Expenses), including VAT Input/Output accounts.

---

### Step 2 — OCR → Categorisation → Ledger Pipeline ✅
*Files: [`app/models/extraction.py`](app/models/extraction.py), [`app/services/ocr/textract_parser.py`](app/services/ocr/textract_parser.py), [`app/services/categorisation/llm_categoriser.py`](app/services/categorisation/llm_categoriser.py), [`app/services/ledger/journal_writer.py`](app/services/ledger/journal_writer.py), [`app/pipeline.py`](app/pipeline.py)*

Three-stage pipeline that converts a raw AWS Textract JSON response into posted ledger entries:

- **Stage 1 — `textract_parser`**: Parses the Textract `AnalyzeExpense` response into a `TextractExpense` Pydantic model. Extracts vendor name, VAT number, document date, invoice number, gross total, and all line items. Confidence is a weighted average across fields (TOTAL 40%, VENDOR 25%, DATE 15%, TAX 10%, line items 20%). The raw JSON is preserved unchanged for the SARS audit trail.
- **Stage 2 — `llm_categoriser`**: Sends all line items to Claude in a single tool-use call. Returns an account code (e.g. `6040`) and VAT code (`SR`/`ZR`/`EX`/`OP`) per line. Account codes are validated against the user's actual Chart of Accounts — unknown codes fall back to `6190` (Sundry Expenses). Combined confidence is the product of OCR and LLM confidence.
- **Stage 3 — `journal_writer`**: Writes atomically in one DB transaction: DR expense lines (net) + DR VAT Input 1200 (VAT, one per SR/ZR item) + CR Bank 1020 (gross total). Auto-posts if combined confidence ≥ 0.85; otherwise leaves as DRAFT for WhatsApp confirmation. The DB balance trigger rejects the post and rolls back all inserts if debits ≠ credits. Accepts an `auto_post=False` flag used by programmatic callers.
- **`pipeline.py`**: Orchestrator. Checks POPIA consent, marks document status through `PROCESSING → EXTRACTED → POSTED/FAILED`, and wires all three stages together. Returns `None` when document type is uncertain — the caller pauses and asks the user.

Key safety decisions: `decimal.Decimal` with `ROUND_HALF_UP` throughout (never `float`); zero-amount lines filtered before DB write; net line items from Textract are grossed up proportionally before writing to ensure the journal always balances; SA bi-monthly VAT periods computed with a dedicated helper.

### Step 3 — WhatsApp Webhook Handler ✅
*Files: [`app/whatsapp/router.py`](app/whatsapp/router.py), [`app/whatsapp/twilio_client.py`](app/whatsapp/twilio_client.py), [`app/whatsapp/media_handler.py`](app/whatsapp/media_handler.py), [`app/whatsapp/message_handler.py`](app/whatsapp/message_handler.py), [`app/main.py`](app/main.py)*

Inbound WhatsApp messages are handled through a state machine in `message_handler.py`. A single DB connection is acquired at the start of each message and held for the full handler, releasing in a `finally` block — compatible with PgBouncer transaction mode.

- **POPIA consent gate** — Every new number gets a consent request before any data is stored. Consent is stamped with timestamp and version on `YES`; a `NO` reply deletes the row entirely.
- **Media message** → validates MIME type (JPEG/PNG/PDF only), downloads from Twilio CDN, uploads to S3 with AES-256 encryption, calls Textract `AnalyzeExpense`, runs the full pipeline. User receives an immediate "Processing…" reply, then a result summary.
- **`YES` / `NO` reply** → looks up the most recent DRAFT journal entry; `YES` triggers the DB balance check and posts it; `NO` enters the structured edit flow.
- **Security** — `router.py` validates the `X-Twilio-Signature` header on every request. Invalid signatures return HTTP 403. FastAPI `BackgroundTasks` ensures Twilio always gets a `200` within its 15-second timeout.

### Step 4 — User Onboarding Flow ✅
*Files: [`db/migrations/004_onboarding_step.sql`](db/migrations/004_onboarding_step.sql), [`app/whatsapp/message_handler.py`](app/whatsapp/message_handler.py)*

A two-step onboarding flow triggered immediately after POPIA consent:

- **Business name** (mandatory) — saved directly from the user's reply.
- **SARS income tax reference** (optional) — `SKIP` escape hatch provided. Not all SA SMEs are formally registered (~72% informal sector per FinScope MSME 2024).
- Data retention set to 7 years on consent — covers SARS TAA s29 (5 years) and Companies Act s24/s28 (7 years).
- The onboarding block sits between the POPIA gate and normal receipt processing — users cannot upload receipts until setup is complete.

### Step 5 — Financial Statement Generation ✅
*Files: [`app/services/reporting/`](app/services/reporting/), [`app/whatsapp/message_handler.py`](app/whatsapp/message_handler.py)*

Users type *REPORT* or *REPORT 2025* to receive a full PDF financial report:

- **Multi-year selection** — on *REPORT*, the bot queries which financial years have posted transactions and presents a numbered list. The user replies with the year; the bot generates that year's report.
- **`report_orchestrator`** — coordinates data fetch, PDF build, S3 upload, and presigned URL delivery.
- **`report_queries`** — fetches Trial Balance, General Ledger (with running balances), P&L, Balance Sheet, VAT201 summary + detail, and Vendor Statements for the requested period.
- **`pdf_builder`** — ReportLab Platypus PDF with word-wrapped cells, explicit "No revenue" messaging when income is absent, and supplier statements filtered to purchases only.
- **`statement_generator`** — `financial_year(fy_end_month, fy_year)` computes exact date boundaries for any SA financial year-end, supporting multi-year history.

### Step 6 — Expanded Chart of Accounts ✅
*Files: [`db/migrations/005_expand_chart_of_accounts.sql`](db/migrations/005_expand_chart_of_accounts.sql), [`app/services/categorisation/llm_categoriser.py`](app/services/categorisation/llm_categoriser.py)*

Added 10 commonly-used SA SME expense codes:

- `6200` IT and Software Subscriptions
- `6210` Entertainment and Client Gifts *(50% VAT input limitation — VAT Act s17(2))*
- `6220` Training and Staff Development
- `6230` Cleaning and Pest Control
- `6240` Security and Alarm
- `6250` Packaging and Consumables
- `6260` Courier and Postage
- `6270` Subscriptions and Memberships
- `6280` Skills Development Levy *(VAT: OP)*
- `6290` COIDA / Workmen's Compensation *(VAT: OP)*

The LLM categoriser system prompt was updated to match. Prompt injection hardening added: receipt data is wrapped in `<receipt>` XML tags with an explicit guard instruction.

### Step 7 — Purchase vs. Sale Classification ✅
*Files: [`app/services/classification/document_classifier.py`](app/services/classification/document_classifier.py), [`app/services/categorisation/revenue_categoriser.py`](app/services/categorisation/revenue_categoriser.py), [`app/services/ledger/journal_writer.py`](app/services/ledger/journal_writer.py), [`db/migrations/008_document_type.sql`](db/migrations/008_document_type.sql), [`db/migrations/009_classification_state.sql`](db/migrations/009_classification_state.sql)*

The pipeline distinguishes between documents the business received (purchases) and documents it issued (sales), routing each through the correct categoriser and journal writer:

- **`document_classifier`** — two-stage classification:
  1. *Heuristic*: if the vendor name on the document contains significant words from the user's business name → confident SALE (no LLM call).
  2. *LLM tool-use* (claude-haiku): returns `PURCHASE`, `SALE`, or `UNCERTAIN` with a confidence score. If confidence < 0.70 → returns `None`; the pipeline pauses and asks the user to reply `EXPENSE` or `INCOME`.

- **`AWAITING_DOCUMENT_TYPE` conversation state** — when classification is uncertain, the user is asked to clarify. The bot stores `pending_document_id` on the user row, then resumes processing via `resume_document_with_type()` once the user replies. State is cleared before resuming so a processing failure never leaves the user stuck.

- **Purchase path**: DR Expense (net) + DR VAT Input 1200 / CR Bank 1020. Creates `INPUT` vat_entries.

- **Sale path** — two variants depending on payment status (see Step 12):
  - `write_sale`: DR Bank 1020 (gross) / CR Revenue 4xxx (net) + CR VAT Output 2100 — cash received.
  - `write_sale_on_credit`: DR Trade Debtors 1110 (gross) / CR Revenue 4xxx (net) + CR VAT Output 2100 — payment outstanding.
  - Both create `OUTPUT` vat_entries. Revenue accounts mapped via `revenue_categoriser` using 4xxx codes.

- Both purchase and sale paths leave entries as DRAFT for WhatsApp confirmation — the user sees a full line-by-line breakdown before replying YES/NO.

### Step 8 — Structured Edit & Correction Flow ✅
*Files: [`app/whatsapp/message_handler.py`](app/whatsapp/message_handler.py), [`db/migrations/010_edit_states.sql`](db/migrations/010_edit_states.sql)*

After a receipt is recorded, the user can correct it via a structured menu — not free text — to keep LLM costs bounded and correction quality high.

**User journey:**

1. Every upload shows a full line-by-line breakdown and always asks for confirmation:
   > 📋 *Here's what I recorded:*
   > 🧾 *Shell Garage — 15 Apr 2025*
   >   Fuel and Vehicle Exp — R 773.91
   >   VAT (15%) — R 116.09
   >   ──────────────────────────────
   >   Total: R 890.00
   >
   > *Is this correct? Reply YES to save it or NO if something's wrong.*

2. User types `EDIT` or replies `NO` → structured correction menu with 5 options:
   > **1** — Wrong type (recorded as expense but it's income, or vice versa)
   > **2** — The amount is wrong
   > **3** — The company or person name is wrong
   > **4** — Wrong category (e.g. should be fuel, not stationery)
   > **5** — Remove it completely

3. Each option routes to a scoped follow-up:
   - **1** → reverses the entry, re-asks EXPENSE or INCOME (full pipeline reruns)
   - **2** → "What's the correct amount?" → reverses + re-records at corrected total
   - **3** → "What's the correct name?" → updates `vendor_name` in-place, no reversal needed
   - **4** → "What is this for?" → one LLM re-categorisation call → reverses + re-posts
   - **5** → creates reversing entry immediately, no LLM call

**Reversing entries**: posted entries are immutable (DB trigger). All corrections work by posting an equal-and-opposite reversing journal entry, then creating a new correct entry. This preserves the full audit trail as required by SARS TAA s29.

**DB fields** ([`010_edit_states.sql`](db/migrations/010_edit_states.sql)):
- `conversation_state` enum: added `AWAITING_EDIT_CHOICE`, `AWAITING_CORRECT_AMOUNT`, `AWAITING_CORRECT_COUNTERPARTY`
- `users.pending_entry_id UUID` — tracks which journal entry is being corrected

### Step 9 — "Your Books" Quick Commands ✅
*Files: [`app/whatsapp/message_handler.py`](app/whatsapp/message_handler.py)*

Four lightweight commands give users an instant view of their books without generating a full PDF report:

| Command | Response |
|---|---|
| `BALANCE` | Income vs expenses for the current calendar month — a quick P&L snapshot |
| `LAST` | Full details of the most recently recorded entry (vendor, amount, category, status) |
| `TOP` | Top 5 expense categories by spend for the current month |
| `PENDING` | All DRAFT entries awaiting confirmation, with vendor and amount |

All four commands query `v_account_balances` directly — no LLM calls, no S3 calls, sub-second responses.

### Step 10 — FastAPI Application Shell ✅
*Files: [`app/main.py`](app/main.py), [`app/db/connection.py`](app/db/connection.py), [`app/whatsapp/router.py`](app/whatsapp/router.py)*

- FastAPI app with a single `/webhook/whatsapp` POST endpoint.
- `asyncpg` connection pool initialised on startup via `lifespan` context manager.
- Twilio signature validation middleware on every inbound request.
- All heavy processing (OCR, LLM, DB writes) runs in `BackgroundTasks` so Twilio always receives a `200` within its 15-second timeout.
- `dev.py` local runner: uvicorn + ngrok tunnel instructions for Twilio sandbox testing.

### Step 11 — Bulk Ingest & Test Data ✅
*Files: [`tests/bulk_ingest.py`](tests/bulk_ingest.py), [`generate_receipts.py`](generate_receipts.py), [`generate_sales_invoices.py`](generate_sales_invoices.py), [`generate_sales_receipts.py`](generate_sales_receipts.py)*

Scripts for generating and ingesting realistic test data:

- **`generate_receipts.py`** — 175 expense receipts/invoices (fuel, supermarket, stationery, utilities, restaurants) issued by real SA vendors.
- **`generate_sales_receipts.py`** — 100 sales receipts issued *by* the test business (`Baba`) to customers, covering consulting, training, and admin services. Business name appears as the vendor/issuer so the classifier heuristic fires correctly.
- **`generate_sales_invoices.py`** — 75 formal B2B tax invoices issued by the test business.
- **`tests/bulk_ingest.py`** — feeds any folder of PDFs/images directly into the full pipeline (S3 → Textract → Claude → ledger), bypassing WhatsApp. Supports `--dry-run` and `--delay` flags.

Usage:
```bash
python generate_sales_receipts.py
python tests/bulk_ingest.py sample_sales_receipts/ +27821234567 --delay 0.5
```

### Step 12 — Income Side & Confirmation UX ✅
*Files: [`app/whatsapp/message_handler.py`](app/whatsapp/message_handler.py), [`app/services/ledger/journal_writer.py`](app/services/ledger/journal_writer.py), [`app/services/categorisation/revenue_categoriser.py`](app/services/categorisation/revenue_categoriser.py), [`app/pipeline.py`](app/pipeline.py), [`db/migrations/011_payment_state.sql`](db/migrations/011_payment_state.sql), [`db/migrations/012_expand_revenue_coa.sql`](db/migrations/012_expand_revenue_coa.sql), [`db/migrations/013_cash_sale_state.sql`](db/migrations/013_cash_sale_state.sql)*

**Payment status question for sales:**
When a document is classified as a SALE, the pipeline pauses before writing and asks whether payment has been received. This determines which journal path is used and ensures IFRS accrual accounting is correct:
- *YES (paid)* → `write_sale`: DR Bank 1020 / CR Revenue + CR VAT Output
- *NO (outstanding)* → `write_sale_on_credit`: DR Trade Debtors 1110 / CR Revenue + CR VAT Output

**Expanded revenue Chart of Accounts** ([`012_expand_revenue_coa.sql`](db/migrations/012_expand_revenue_coa.sql)):
Seven new revenue codes added to cover common SA SME income types:

| Code | Name |
|---|---|
| `4050` | Consulting and Professional Fees |
| `4060` | Commission and Agency Income |
| `4070` | Rental Income |
| `4080` | Catering and Food Sales |
| `4090` | Contract and Project Income |
| `4100` | Maintenance and Repair Services |
| `4110` | Freight and Delivery Income |

The `revenue_categoriser` system prompt was rewritten with descriptions for all 11 revenue codes. New codes are seeded into all existing users' accounts automatically.

**Cash sale without a document (`CASH SALE` command):**
For market stall vendors, caterers, and others who collect cash without issuing a formal document:
1. User types `CASH SALE`
2. Bot asks: *"What did you sell?"* — plain text description
3. Bot asks: *"What was the total amount received?"*
4. claude-haiku categorises the description into the correct 4xxx revenue code and back-calculates net + VAT (gross ÷ 1.15)
5. Full breakdown shown, user confirms with YES/NO

`write_cash_sale()` writes the entry with `document_id = NULL` — the schema already permitted this for manual adjustments. VAT entries are created as OUTPUT type.

**DB additions** ([`011_payment_state.sql`](db/migrations/011_payment_state.sql), [`013_cash_sale_state.sql`](db/migrations/013_cash_sale_state.sql)):
- `conversation_state` enum: `AWAITING_PAYMENT_CONFIRMED`, `AWAITING_CORRECT_COUNTERPARTY`, `AWAITING_CASH_SALE_DESCRIPTION`, `AWAITING_CASH_SALE_AMOUNT`
- `users.pending_sale_description TEXT` — holds the cash sale description between conversation turns

---

### Step 13 — Deployment 🔲

Dockerised application, environment configuration, AWS infrastructure (RDS PostgreSQL, S3 bucket, Textract IAM roles), and CI/CD pipeline.
