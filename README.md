# CrediSnap

WhatsApp-based financial statement generator for South African SMEs.
Users upload receipts and invoices via WhatsApp; CrediSnap extracts the data, categorises it, and produces IFRS-aligned P&L and Balance Sheet reports for loan applications.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Interface | WhatsApp Business API (Twilio / Turn.io) |
| Backend | Python — FastAPI or Django |
| Database | PostgreSQL (ledger) + AWS S3 (document storage) |
| AI / OCR | AWS Textract (extraction) + LLM (categorisation) |

## Compliance

- **POPIA** (Act 4 of 2013) — consent tracking, data retention, soft-delete support
- **SARS** — 5-year record-keeping (TAA s29), VAT at 15%, PAYE/UIF fields
- **IFRS for SMEs** — double-entry ledger, P&L and Balance Sheet aligned to IFRS line items
- **Currency** — ZAR throughout

---

## Build Progress

### Step 1 — Database Schema ✅
*Files: [`db/migrations/001_initial_schema.sql`](db/migrations/001_initial_schema.sql), [`db/migrations/002_seed_chart_of_accounts.sql`](db/migrations/002_seed_chart_of_accounts.sql)*

PostgreSQL schema covering the full data model:

- **`users`** — SME business accounts identified by WhatsApp number. Includes CIPC registration, SARS VAT/tax references, POPIA consent fields, and a configurable financial year-end month.
- **`accounts`** — Chart of Accounts per user, seeded from a standard SA SME template (`account_templates`). Hierarchical (self-referencing `parent_id`), with an `ifrs_line_item` column that drives automated financial statement grouping.
- **`documents`** — Every file uploaded via WhatsApp. Stores the S3 location, Textract job ID, raw OCR JSON (immutable audit trail), structured extraction output, and a `PENDING → PROCESSING → EXTRACTED → POSTED` status lifecycle.
- **`journal_entries`** — One row per financial event. Links back to its source document. AI-generated entries start as `DRAFT`; a confidence threshold either auto-posts them or sends a WhatsApp confirmation to the user.
- **`journal_entry_lines`** — The double-entry core. Each line carries a debit or credit amount (never both). A DB-level trigger enforces that debits = credits before any entry can transition to `POSTED`. A second trigger makes posted entries immutable — corrections require reversing entries, preserving the audit trail.
- **`v_account_balances`** — View that aggregates posted lines by account and calendar month. Financial statement queries are a simple `GROUP BY ifrs_line_item` against this view.

The standard SA SME Chart of Accounts covers account codes 1000–6999 (Assets → Liabilities → Equity → Revenue → Cost of Sales → Operating Expenses), including VAT Input/Output accounts for SARS VAT201 compliance.

- **`vat_entries`** ([`003_vat_entries.sql`](db/migrations/003_vat_entries.sql)) — One row per VATable transaction. Links the gross/net/VAT amounts, VAT code (SR/ZR/EX/OP), supplier VAT number, and tax period back to both the journal entry and the specific VAT line within it. DB constraints enforce that `net + vat = gross` and that standard-rate entries always carry a non-zero VAT amount. The `v_vat201_summary` view aggregates these per bi-monthly period to produce the Output VAT, Input VAT, and net payable/refundable figures needed for the SARS VAT201 return.

---

### Step 2 — OCR → Categorisation → Ledger Pipeline ✅
*Files: [`app/models/extraction.py`](app/models/extraction.py), [`app/services/ocr/textract_parser.py`](app/services/ocr/textract_parser.py), [`app/services/categorisation/llm_categoriser.py`](app/services/categorisation/llm_categoriser.py), [`app/services/ledger/journal_writer.py`](app/services/ledger/journal_writer.py), [`app/pipeline.py`](app/pipeline.py)*

Three-stage pipeline that converts a raw AWS Textract JSON response into posted ledger entries:

- **Stage 1 — `textract_parser`**: Parses the Textract `AnalyzeExpense` response into a `TextractExpense` Pydantic model. Extracts vendor name, vendor VAT number, document date, invoice number, gross total, and all line items. Confidence is the minimum across all extracted fields. The full raw JSON is preserved unchanged for the SARS audit trail.
- **Stage 2 — `llm_categoriser`**: Sends all line items to Claude in a single tool-use call. Returns an account code (e.g. `6040`) and VAT code (`SR`/`ZR`/`EX`/`OP`) per line. Account codes are validated against the user's actual Chart of Accounts before the model is passed forward — unknown codes fall back to `6190` (Sundry Expenses). Combined confidence is `min(ocr_confidence, llm_confidence)`.
- **Stage 3 — `journal_writer`**: Writes atomically in one DB transaction: DR expense lines (net) + DR VAT Input 1200 (VAT amount, one per SR/ZR item) + CR Bank 1020 (gross total). `vat_journal_line_id` is captured via `RETURNING id` — no separate SELECT. Auto-posts if combined confidence ≥ 0.85; otherwise leaves as DRAFT for WhatsApp confirmation. The DB trigger validates debit = credit on post; a failure rolls back all inserts.
- **`pipeline.py`**: Orchestrator. Checks POPIA consent, marks document status through `PROCESSING → EXTRACTED → POSTED/FAILED`, and wires all three stages together.

Key safety decisions: `decimal.Decimal` with `ROUND_HALF_UP` throughout (never `float`); zero-amount lines are filtered before DB write; generated columns `period_month`/`period_year` are excluded from INSERTs; SA bi-monthly VAT periods are computed with a dedicated helper.

### Step 3 — WhatsApp Webhook Handler ✅
*Files: [`app/whatsapp/router.py`](app/whatsapp/router.py), [`app/whatsapp/twilio_client.py`](app/whatsapp/twilio_client.py), [`app/whatsapp/media_handler.py`](app/whatsapp/media_handler.py), [`app/whatsapp/message_handler.py`](app/whatsapp/message_handler.py), [`app/main.py`](app/main.py)*

Inbound WhatsApp messages are handled through a state machine in `message_handler.py`:

- **POPIA consent gate** — Every new number gets a consent request before any data is stored. The skeleton user row (`popia_consent_given=FALSE`) is created on first contact; consent is stamped with timestamp and version on `YES`; a `NO` reply deletes the row entirely.
- **Media message** → validates MIME type (JPEG/PNG/PDF only), downloads from Twilio CDN with auth, uploads to S3 with AES-256 server-side encryption, creates a `documents` row, calls Textract `AnalyzeExpense`, then hands off to `pipeline.process_document`. User receives an immediate "Processing…" reply, then a result summary once the pipeline completes.
- **`YES` / `NO` reply** → looks up the user's most recent `DRAFT` journal entry; `YES` fires the `UPDATE status='POSTED'` (triggering the DB balance check); `NO` deletes the entry and marks the document `REJECTED`.
- **Security** — `router.py` validates the `X-Twilio-Signature` header on every request before any processing. Invalid signatures return HTTP 403. The background task pattern (FastAPI `BackgroundTasks`) ensures Twilio always gets a `200` within its 15-second timeout.

### Step 4 — User Onboarding Flow ✅
*Files: [`db/migrations/004_onboarding_step.sql`](db/migrations/004_onboarding_step.sql), [`app/whatsapp/message_handler.py`](app/whatsapp/message_handler.py)*

A two-step onboarding flow triggered immediately after POPIA consent is granted:

- **`onboarding_step` column** ([`004_onboarding_step.sql`](db/migrations/004_onboarding_step.sql)) — Adds a `BUSINESS_NAME → TAX_REF → DONE` enum to `users`. `NULL` on existing rows is treated as `DONE` so legacy users are unaffected.
- **Business name** (mandatory) — Bot asks "What is your business name?" and saves the reply directly. Any non-empty text is accepted.
- **SARS income tax reference** (optional) — Bot asks for the reference number with a `SKIP` escape hatch. Not all SA SMEs are formally tax-registered (~72% informal sector per FinScope MSME 2024), so this is intentionally skippable.
- **Data retention** — Updated from 5 to 7 years on consent to cover both the SARS TAA s29 requirement (5 years) and the Companies Act s24/s28 requirement (7 years) for registered entities.
- The onboarding block sits between the POPIA gate and the normal receipt-processing flow in the state machine — users in either onboarding step cannot send receipts until setup is complete.

### Step 5 — Financial Statement Generation 🔲

Query `v_account_balances` to produce a formatted P&L and Balance Sheet for a given date range. Output as WhatsApp message (summary) and PDF (full report).

### Step 6 — FastAPI Application Shell 🔲

Project structure, configuration management, database connection layer, and dependency injection wiring all components together.

### Step 7 — Authentication & Security 🔲

WhatsApp number verification, POPIA consent gate on first message, rate limiting, and S3 pre-signed URL handling.

### Step 8 — Deployment 🔲

Dockerised application, environment configuration, AWS infrastructure (RDS PostgreSQL, S3 bucket, Textract IAM roles), and CI/CD pipeline.
