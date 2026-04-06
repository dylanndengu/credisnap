-- =============================================================================
-- CrediSnap: Document classification conversation state
-- Version: 009
-- Adds AWAITING_DOCUMENT_TYPE to conversation_state and a pending_document_id
-- column to users so the pipeline can pause and ask the user to clarify
-- whether an ambiguous document is a purchase or a sale.
-- =============================================================================

ALTER TYPE conversation_state ADD VALUE 'AWAITING_DOCUMENT_TYPE';

ALTER TABLE users
    ADD COLUMN pending_document_id UUID REFERENCES documents(id) ON DELETE SET NULL;

COMMENT ON COLUMN users.pending_document_id IS
    'Set when conversation_state = AWAITING_DOCUMENT_TYPE. Points to the document '
    'whose type the user needs to clarify (EXPENSE or INCOME).';
