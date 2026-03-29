-- =============================================================================
-- CrediSnap: Initial Database Schema
-- Version: 001
-- Compliance: IFRS, South African POPIA, SARS record-keeping (5-year retention)
-- Currency: ZAR (South African Rand)
-- =============================================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- ENUMS
-- =============================================================================

CREATE TYPE account_type AS ENUM (
    'ASSET',        -- Balance Sheet: owned resources
    'LIABILITY',    -- Balance Sheet: owed obligations
    'EQUITY',       -- Balance Sheet: owner's interest
    'REVENUE',      -- P&L: income earned
    'EXPENSE'       -- P&L: costs incurred
);

-- Per double-entry rules: Assets/Expenses have DEBIT normal balance;
-- Liabilities/Equity/Revenue have CREDIT normal balance.
CREATE TYPE normal_balance_type AS ENUM ('DEBIT', 'CREDIT');

CREATE TYPE document_type AS ENUM (
    'RECEIPT',
    'INVOICE_RECEIVED',     -- Accounts Payable
    'INVOICE_ISSUED',       -- Accounts Receivable
    'BANK_STATEMENT',
    'OTHER'
);

CREATE TYPE document_status AS ENUM (
    'PENDING',      -- Received from WhatsApp, awaiting processing
    'PROCESSING',   -- Textract job in progress
    'EXTRACTED',    -- OCR done, awaiting LLM categorisation
    'POSTED',       -- Journal entry created and posted to ledger
    'FAILED',       -- Processing error
    'REJECTED'      -- Unreadable or out of scope
);

CREATE TYPE entry_status AS ENUM (
    'DRAFT',        -- Awaiting review (AI-generated entries start here)
    'POSTED'        -- Immutable, included in financial statements
);


-- =============================================================================
-- 1. USERS (SMEs)
-- =============================================================================

CREATE TABLE users (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Primary contact channel
    whatsapp_number         VARCHAR(20)  NOT NULL UNIQUE,   -- E.164 format: +27821234567

    -- Business identity
    business_name           VARCHAR(255) NOT NULL,
    trading_name            VARCHAR(255),
    cipc_reg_number         VARCHAR(20)  UNIQUE,            -- Companies & Intellectual Property Commission
    vat_number              VARCHAR(15)  UNIQUE,            -- SARS VAT registration (prefix: 4xxxxxxxx)
    income_tax_ref          VARCHAR(15),                    -- SARS income tax reference

    -- Contact
    contact_email           VARCHAR(255),
    contact_name            VARCHAR(255),

    -- Financial year (SARS allows non-calendar year-ends)
    financial_year_end_month SMALLINT NOT NULL DEFAULT 2    -- February default (SARS common)
                            CHECK (financial_year_end_month BETWEEN 1 AND 12),

    -- POPIA Compliance (Protection of Personal Information Act, Act 4 of 2013)
    popia_consent_given     BOOLEAN     NOT NULL DEFAULT FALSE,
    popia_consent_at        TIMESTAMPTZ,                    -- Must be set when consent = TRUE
    popia_consent_version   VARCHAR(10),                    -- Track consent form version
    data_retention_until    DATE,                           -- SARS requires 5 years from assessment

    -- Audit
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT popia_consent_consistency
        CHECK (popia_consent_given = FALSE OR popia_consent_at IS NOT NULL)
);

COMMENT ON TABLE users IS
    'SME business accounts. WhatsApp number is the primary identifier for inbound messages.';
COMMENT ON COLUMN users.vat_number IS
    'SARS VAT number — required for VAT input/output tracking. Null = not VAT registered.';
COMMENT ON COLUMN users.data_retention_until IS
    'SARS s29 of TAA: records must be kept for 5 years after the date of submission of the relevant return.';


-- =============================================================================
-- 2. CHART OF ACCOUNTS
-- =============================================================================
-- A master list of accounts seeded per user at onboarding.
-- Structured as a self-referencing tree to support hierarchical reporting
-- (e.g., Operating Expenses > Salaries > PAYE).

CREATE TABLE accounts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- Standard account code (e.g., 1000=Cash, 4000=Revenue, 6000=Expenses)
    code                VARCHAR(20) NOT NULL,
    name                VARCHAR(255) NOT NULL,

    account_type        account_type        NOT NULL,
    normal_balance      normal_balance_type NOT NULL,

    -- Hierarchy: NULL parent = top-level account
    parent_id           UUID REFERENCES accounts(id) ON DELETE RESTRICT,

    -- IFRS presentation line mapping
    -- (e.g., 'current_assets', 'cost_of_sales', 'admin_expenses')
    ifrs_line_item      VARCHAR(100),

    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (user_id, code)
);

COMMENT ON TABLE accounts IS
    'Chart of Accounts per SME. Seeded from a standard SA SME template on user creation.';
COMMENT ON COLUMN accounts.normal_balance IS
    'The side (DEBIT/CREDIT) that increases this account. Derived from account_type but stored
     explicitly to simplify ledger balance queries without conditional logic.';
COMMENT ON COLUMN accounts.ifrs_line_item IS
    'Maps this account to a named line on the IFRS financial statements for automated report generation.';


-- =============================================================================
-- 3. DOCUMENTS (WhatsApp Uploads)
-- =============================================================================

CREATE TABLE documents (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- Storage
    s3_bucket           VARCHAR(100) NOT NULL,
    s3_key              VARCHAR(500) NOT NULL,              -- Path within bucket
    s3_etag             VARCHAR(100),                       -- For integrity verification
    mime_type           VARCHAR(50),                        -- image/jpeg, image/png, application/pdf
    file_size_bytes     BIGINT,

    -- Source traceability (WhatsApp)
    whatsapp_message_id VARCHAR(100) UNIQUE,                -- Twilio/Turn.io message SID
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Classification
    document_type       document_type   NOT NULL DEFAULT 'RECEIPT',
    status              document_status NOT NULL DEFAULT 'PENDING',

    -- OCR / Extraction output (raw Textract JSON for audit trail)
    textract_job_id     VARCHAR(100),
    ocr_raw_json        JSONB,                              -- Full Textract response
    extracted_data      JSONB,                              -- Cleaned/structured extraction
    extraction_confidence NUMERIC(5,4)                      -- 0.0000 – 1.0000
                        CHECK (extraction_confidence BETWEEN 0 AND 1),

    -- Extracted document metadata
    vendor_name         VARCHAR(255),
    document_date       DATE,
    document_ref        VARCHAR(100),                       -- Invoice/receipt number from document
    gross_amount        NUMERIC(15,2),                      -- Total inc. VAT
    vat_amount          NUMERIC(15,2),                      -- VAT portion (15% in SA)
    net_amount          NUMERIC(15,2),                      -- Total excl. VAT

    -- Error tracking
    error_message       TEXT,
    retry_count         SMALLINT NOT NULL DEFAULT 0,

    -- POPIA: documents can be flagged for deletion after retention period
    retention_until     DATE,
    deleted_at          TIMESTAMPTZ,                        -- Soft delete for POPIA erasure requests

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE documents IS
    'Every file uploaded by a user. Stores S3 location, Textract results, and extracted financials.';
COMMENT ON COLUMN documents.ocr_raw_json IS
    'Full raw Textract response preserved for audit. Never mutated after write.';
COMMENT ON COLUMN documents.extracted_data IS
    'LLM-structured output: line items with account codes, amounts, VAT flags.';


-- =============================================================================
-- 4. JOURNAL ENTRIES (Transaction Headers)
-- =============================================================================
-- Each document that clears processing creates one Journal Entry.
-- Manual adjustments also create Journal Entries (document_id = NULL).
-- RULE: A posted entry is IMMUTABLE. Corrections use reversing entries.

CREATE TABLE journal_entries (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    document_id         UUID REFERENCES documents(id) ON DELETE SET NULL,

    -- Accounting period
    entry_date          DATE        NOT NULL,               -- Transaction date (from document)
    period_month        SMALLINT    NOT NULL                -- For period-based reporting
                        GENERATED ALWAYS AS (EXTRACT(MONTH FROM entry_date)::SMALLINT) STORED,
    period_year         SMALLINT    NOT NULL
                        GENERATED ALWAYS AS (EXTRACT(YEAR  FROM entry_date)::SMALLINT) STORED,

    -- Identity
    reference_number    VARCHAR(100),                       -- Internal reference
    description         TEXT        NOT NULL,
    status              entry_status NOT NULL DEFAULT 'DRAFT',

    -- Source tracking
    is_ai_generated     BOOLEAN     NOT NULL DEFAULT FALSE, -- TRUE = LLM proposed this entry
    ai_confidence       NUMERIC(5,4)
                        CHECK (ai_confidence BETWEEN 0 AND 1),
    reviewed_by         UUID REFERENCES users(id),          -- NULL = auto-approved
    reviewed_at         TIMESTAMPTZ,

    -- Immutability guard: once POSTED, this flag is set and entry cannot be modified
    posted_at           TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT posted_requires_timestamp
        CHECK (status != 'POSTED' OR posted_at IS NOT NULL)
);

COMMENT ON TABLE journal_entries IS
    'One entry per financial event. Links back to the source document.
     AI-generated entries start as DRAFT and are auto-posted above a confidence threshold,
     or queued for user confirmation via WhatsApp.';


-- =============================================================================
-- 5. JOURNAL ENTRY LINES (The Double-Entry Core)
-- =============================================================================
-- This is the heart of the ledger.
-- INVARIANT: For every journal_entry_id,
--   SUM(debit_amount) = SUM(credit_amount)
-- This constraint is enforced by a trigger (see below).

CREATE TABLE journal_entry_lines (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    journal_entry_id    UUID NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    account_id          UUID NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,

    -- Exactly ONE of these must be non-zero per line
    debit_amount        NUMERIC(15,2) NOT NULL DEFAULT 0
                        CHECK (debit_amount >= 0),
    credit_amount       NUMERIC(15,2) NOT NULL DEFAULT 0
                        CHECK (credit_amount >= 0),

    -- VAT tracking (SARS VAT201 compliance)
    vat_amount          NUMERIC(15,2) NOT NULL DEFAULT 0
                        CHECK (vat_amount >= 0),
    -- SA VAT codes: SR=Standard Rate 15%, ZR=Zero Rated, EX=Exempt, OP=Out of Scope
    vat_code            VARCHAR(5),

    description         VARCHAR(500),
    line_order          SMALLINT NOT NULL DEFAULT 0,        -- Presentation order

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Each line must have value on exactly one side
    CONSTRAINT debit_or_credit_not_both
        CHECK (
            (debit_amount > 0 AND credit_amount = 0) OR
            (credit_amount > 0 AND debit_amount = 0)
        )
);

COMMENT ON TABLE journal_entry_lines IS
    'Individual debit/credit lines. Every posted entry must have balanced debits = credits.
     Account balances for financial statements are derived by aggregating these lines.';


-- =============================================================================
-- 6. TRIGGER: ENFORCE DOUBLE-ENTRY BALANCE ON POST
-- =============================================================================
-- Prevents a journal entry from transitioning to POSTED unless it balances.

CREATE OR REPLACE FUNCTION check_journal_entry_balance()
RETURNS TRIGGER AS $$
DECLARE
    v_total_debits  NUMERIC(15,2);
    v_total_credits NUMERIC(15,2);
BEGIN
    -- Only enforce on transition to POSTED
    IF NEW.status = 'POSTED' AND (OLD.status IS DISTINCT FROM 'POSTED') THEN
        SELECT
            COALESCE(SUM(debit_amount),  0),
            COALESCE(SUM(credit_amount), 0)
        INTO v_total_debits, v_total_credits
        FROM journal_entry_lines
        WHERE journal_entry_id = NEW.id;

        IF v_total_debits = 0 AND v_total_credits = 0 THEN
            RAISE EXCEPTION
                'Journal entry % has no lines. Cannot post an empty entry.', NEW.id;
        END IF;

        IF v_total_debits <> v_total_credits THEN
            RAISE EXCEPTION
                'Journal entry % does not balance. Debits: % | Credits: % | Difference: %',
                NEW.id,
                v_total_debits,
                v_total_credits,
                ABS(v_total_debits - v_total_credits);
        END IF;

        -- Stamp the posted timestamp
        NEW.posted_at := NOW();
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_journal_entry_balance
    BEFORE UPDATE OF status ON journal_entries
    FOR EACH ROW
    EXECUTE FUNCTION check_journal_entry_balance();


-- =============================================================================
-- 7. TRIGGER: PREVENT MODIFICATION OF POSTED ENTRIES
-- =============================================================================

CREATE OR REPLACE FUNCTION prevent_posted_entry_mutation()
RETURNS TRIGGER AS $$
DECLARE
    v_status entry_status;
BEGIN
    SELECT status INTO v_status
    FROM journal_entries
    WHERE id = COALESCE(NEW.journal_entry_id, OLD.journal_entry_id);

    IF v_status = 'POSTED' THEN
        RAISE EXCEPTION
            'Cannot modify lines of a POSTED journal entry. Create a reversing entry instead.';
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_prevent_posted_line_insert
    BEFORE INSERT ON journal_entry_lines
    FOR EACH ROW
    EXECUTE FUNCTION prevent_posted_entry_mutation();

CREATE TRIGGER trg_prevent_posted_line_update
    BEFORE UPDATE ON journal_entry_lines
    FOR EACH ROW
    EXECUTE FUNCTION prevent_posted_entry_mutation();

CREATE TRIGGER trg_prevent_posted_line_delete
    BEFORE DELETE ON journal_entry_lines
    FOR EACH ROW
    EXECUTE FUNCTION prevent_posted_entry_mutation();


-- =============================================================================
-- 8. INDEXES
-- =============================================================================

-- Users
CREATE INDEX idx_users_whatsapp       ON users(whatsapp_number);

-- Documents
CREATE INDEX idx_documents_user       ON documents(user_id);
CREATE INDEX idx_documents_status     ON documents(status);
CREATE INDEX idx_documents_date       ON documents(document_date);
CREATE INDEX idx_documents_received   ON documents(received_at);

-- Journal Entries
CREATE INDEX idx_je_user              ON journal_entries(user_id);
CREATE INDEX idx_je_period            ON journal_entries(user_id, period_year, period_month);
CREATE INDEX idx_je_date              ON journal_entries(entry_date);
CREATE INDEX idx_je_status            ON journal_entries(status);
CREATE INDEX idx_je_document          ON journal_entries(document_id);

-- Journal Entry Lines  (most critical — drives all financial statement queries)
CREATE INDEX idx_jel_entry            ON journal_entry_lines(journal_entry_id);
CREATE INDEX idx_jel_account          ON journal_entry_lines(account_id);


-- =============================================================================
-- 9. ACCOUNT BALANCE VIEW (P&L and Balance Sheet driver)
-- =============================================================================
-- Aggregates posted lines per account per period.
-- Financial statement queries read from this view.

CREATE VIEW v_account_balances AS
SELECT
    a.user_id,
    a.id                        AS account_id,
    a.code                      AS account_code,
    a.name                      AS account_name,
    a.account_type,
    a.normal_balance,
    a.ifrs_line_item,
    je.period_year,
    je.period_month,
    SUM(jel.debit_amount)       AS total_debits,
    SUM(jel.credit_amount)      AS total_credits,
    -- Net balance expressed in the account's natural direction
    CASE a.normal_balance
        WHEN 'DEBIT'  THEN SUM(jel.debit_amount)  - SUM(jel.credit_amount)
        WHEN 'CREDIT' THEN SUM(jel.credit_amount) - SUM(jel.debit_amount)
    END                         AS balance
FROM journal_entry_lines jel
JOIN journal_entries     je  ON je.id      = jel.journal_entry_id
JOIN accounts            a   ON a.id       = jel.account_id
WHERE je.status = 'POSTED'
GROUP BY
    a.user_id, a.id, a.code, a.name,
    a.account_type, a.normal_balance, a.ifrs_line_item,
    je.period_year, je.period_month;

COMMENT ON VIEW v_account_balances IS
    'Aggregated posted balances per account per calendar month.
     Join with accounts.ifrs_line_item to group into P&L or Balance Sheet line items.';
