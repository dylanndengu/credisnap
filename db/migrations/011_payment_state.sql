-- =============================================================================
-- CrediSnap: Payment confirmation and counterparty correction states
-- Version: 011
-- =============================================================================
-- AWAITING_PAYMENT_CONFIRMED: sale document classified — pausing to ask
--   whether payment has already been received (bank) or is still outstanding
--   (debtors). Determines whether to debit 1020 Bank or 1110 Trade Debtors.
--
-- AWAITING_CORRECT_COUNTERPARTY: user flagged the company/person name on an
--   entry as wrong. Waiting for the corrected name before updating.
-- =============================================================================

ALTER TYPE conversation_state ADD VALUE IF NOT EXISTS 'AWAITING_PAYMENT_CONFIRMED';
ALTER TYPE conversation_state ADD VALUE IF NOT EXISTS 'AWAITING_CORRECT_COUNTERPARTY';
