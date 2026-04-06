-- =============================================================================
-- CrediSnap: Document Type
-- Version: 008
-- Adds document_type to documents so the pipeline can distinguish purchases
-- (expenses) from sales (income) and route them through the correct journal
-- writer path.
-- =============================================================================

CREATE TYPE document_type AS ENUM ('PURCHASE', 'SALE');

ALTER TABLE documents
    ADD COLUMN document_type document_type NOT NULL DEFAULT 'PURCHASE';

COMMENT ON COLUMN documents.document_type IS
    'PURCHASE = receipt/supplier invoice (we are the buyer). '
    'SALE = sales invoice (we are the seller).';
