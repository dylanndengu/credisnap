-- =============================================================================
-- CrediSnap: VAT Entries Table
-- Version: 003
-- Purpose: Explicit VAT audit trail per transaction for SARS VAT201 reporting.
-- Each VATable document produces exactly one row here, linking the gross amount,
-- net amount, VAT amount, and VAT code back to the journal entry and document.
-- =============================================================================

-- SA VAT transaction types for VAT201 categorisation
CREATE TYPE vat_transaction_type AS ENUM (
    'INPUT',    -- VAT paid to supplier (purchases) — reduces VAT liability
    'OUTPUT'    -- VAT charged to customer (sales)  — increases VAT liability
);

-- SA VAT codes (SARS VAT404 guide)
CREATE TYPE vat_code AS ENUM (
    'SR',   -- Standard Rate: 15%
    'ZR',   -- Zero Rated: 0% (e.g. basic foodstuffs, exports)
    'EX',   -- Exempt: no VAT (e.g. residential rent, financial services)
    'OP'    -- Out of Scope: not subject to VAT at all
);

CREATE TABLE vat_entries (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- The journal entry this VAT belongs to
    journal_entry_id        UUID NOT NULL REFERENCES journal_entries(id) ON DELETE RESTRICT,

    -- The specific line on that entry that holds the VAT debit/credit
    -- (i.e. the line posting to account 1200 VAT Input or 2100 VAT Output)
    vat_journal_line_id     UUID NOT NULL REFERENCES journal_entry_lines(id) ON DELETE RESTRICT,

    -- The source document for full traceability
    document_id             UUID REFERENCES documents(id) ON DELETE SET NULL,

    -- VAT classification
    transaction_type        vat_transaction_type NOT NULL,
    vat_code                vat_code NOT NULL DEFAULT 'SR',

    -- Amounts — always stored excl. VAT / VAT only / incl. VAT
    net_amount              NUMERIC(15,2) NOT NULL CHECK (net_amount >= 0),   -- excl. VAT
    vat_amount              NUMERIC(15,2) NOT NULL CHECK (vat_amount >= 0),   -- VAT portion only
    gross_amount            NUMERIC(15,2) NOT NULL CHECK (gross_amount >= 0), -- incl. VAT

    -- Effective VAT rate (stored for audit; should equal vat_amount / net_amount)
    vat_rate                NUMERIC(5,4) NOT NULL DEFAULT 0.15,

    -- Supplier/customer details (from OCR extraction — needed for VAT201 audit)
    counterparty_name       VARCHAR(255),
    counterparty_vat_number VARCHAR(15),    -- Their SARS VAT number if on the invoice
    invoice_number          VARCHAR(100),   -- As printed on the source document
    tax_period              DATE NOT NULL,  -- First day of the VAT period (bi-monthly in SA)

    -- Audit
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Integrity: gross must equal net + vat
    CONSTRAINT gross_equals_net_plus_vat
        CHECK (gross_amount = net_amount + vat_amount),

    -- SR entries must have a non-zero VAT amount; EX/OP must have zero VAT
    CONSTRAINT vat_amount_consistent_with_code
        CHECK (
            (vat_code = 'SR' AND vat_amount > 0) OR
            (vat_code IN ('ZR', 'EX', 'OP') AND vat_amount = 0)
        )
);

COMMENT ON TABLE vat_entries IS
    'One row per VATable transaction. Drives SARS VAT201 return:
     Output VAT (sales) minus Input VAT (purchases) = VAT payable/refundable.
     Linked to both the journal entry and the specific VAT line within it.';

COMMENT ON COLUMN vat_entries.vat_journal_line_id IS
    'Points to the exact journal_entry_line that debited 1200 (Input) or
     credited 2100 (Output). Prevents ambiguity when a journal entry has
     multiple VAT lines (e.g. mixed-rate invoice).';

COMMENT ON COLUMN vat_entries.tax_period IS
    'SA VAT periods are bi-monthly. Store as the first day of the period
     (e.g. 2025-01-01 covers Jan–Feb). Used to group entries per VAT201 submission.';

COMMENT ON COLUMN vat_entries.counterparty_vat_number IS
    'Supplier VAT number as printed on the tax invoice. SARS requires this
     for Input VAT claims above R50. Extracted from OCR output.';


-- =============================================================================
-- INDEXES
-- =============================================================================

CREATE INDEX idx_vat_user_period   ON vat_entries(user_id, tax_period);
CREATE INDEX idx_vat_journal_entry ON vat_entries(journal_entry_id);
CREATE INDEX idx_vat_document      ON vat_entries(document_id);
CREATE INDEX idx_vat_type          ON vat_entries(user_id, transaction_type);


-- =============================================================================
-- VAT201 SUMMARY VIEW
-- =============================================================================
-- Replicates the structure of the SARS VAT201 return fields.
-- Net VAT payable = total output - total input. Negative = refund due.

CREATE VIEW v_vat201_summary AS
SELECT
    ve.user_id,
    ve.tax_period,
    SUM(CASE WHEN ve.transaction_type = 'OUTPUT' THEN ve.net_amount    ELSE 0 END) AS output_net,
    SUM(CASE WHEN ve.transaction_type = 'OUTPUT' THEN ve.vat_amount    ELSE 0 END) AS output_vat,
    SUM(CASE WHEN ve.transaction_type = 'INPUT'  THEN ve.net_amount    ELSE 0 END) AS input_net,
    SUM(CASE WHEN ve.transaction_type = 'INPUT'  THEN ve.vat_amount    ELSE 0 END) AS input_vat,
    SUM(CASE WHEN ve.transaction_type = 'OUTPUT' THEN ve.vat_amount    ELSE 0 END)
  - SUM(CASE WHEN ve.transaction_type = 'INPUT'  THEN ve.vat_amount    ELSE 0 END) AS net_vat_payable
FROM vat_entries ve
JOIN journal_entries je ON je.id = ve.journal_entry_id
WHERE je.status = 'POSTED'
GROUP BY ve.user_id, ve.tax_period;

COMMENT ON VIEW v_vat201_summary IS
    'Per-period VAT201 figures. net_vat_payable > 0 means the business owes SARS;
     net_vat_payable < 0 means SARS owes the business a refund.';
