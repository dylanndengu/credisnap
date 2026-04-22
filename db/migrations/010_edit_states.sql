-- Migration 010: Add EDIT correction flow conversation states and pending_entry_id
--
-- New states:
--   AWAITING_EDIT_CHOICE   — user typed EDIT or NO; showing the 4-option correction menu
--   AWAITING_CORRECT_AMOUNT — user chose "1 — Amount is wrong"; waiting for the correct figure
--
-- AWAITING_CATEGORY_HINT (existing) is reused for option 2 (wrong category)
-- and option 4 (something else).

ALTER TYPE conversation_state ADD VALUE IF NOT EXISTS 'AWAITING_EDIT_CHOICE';
ALTER TYPE conversation_state ADD VALUE IF NOT EXISTS 'AWAITING_CORRECT_AMOUNT';

-- Stores which journal entry is currently being corrected.
-- Set when the user enters the EDIT flow; cleared when correction is complete.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS pending_entry_id UUID REFERENCES journal_entries(id) ON DELETE SET NULL;

COMMENT ON COLUMN users.pending_entry_id IS
    'Set when conversation_state = AWAITING_EDIT_CHOICE / AWAITING_CORRECT_AMOUNT / AWAITING_CATEGORY_HINT. '
    'Points to the journal entry being corrected.';
