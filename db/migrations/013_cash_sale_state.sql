-- =============================================================================
-- CrediSnap: Cash sale conversation states
-- Version: 013
-- Supports recording a cash sale by text (no receipt/invoice to upload).
--
-- AWAITING_CASH_SALE_DESCRIPTION: user typed CASH SALE — waiting for them
--   to describe what they sold in plain text.
-- AWAITING_CASH_SALE_AMOUNT: description captured — waiting for the ZAR amount.
--
-- pending_sale_description holds the description between the two turns so
-- it is available when the amount arrives and the entry is written.
-- =============================================================================

ALTER TYPE conversation_state ADD VALUE IF NOT EXISTS 'AWAITING_CASH_SALE_DESCRIPTION';
ALTER TYPE conversation_state ADD VALUE IF NOT EXISTS 'AWAITING_CASH_SALE_AMOUNT';

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS pending_sale_description TEXT;

COMMENT ON COLUMN users.pending_sale_description IS
    'Set when conversation_state = AWAITING_CASH_SALE_AMOUNT. Holds the '
    'user-provided description of what was sold; cleared once the entry is written.';
