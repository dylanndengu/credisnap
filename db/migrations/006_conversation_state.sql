-- =============================================================================
-- CrediSnap: Conversation State
-- Version: 006
-- Adds conversation_state to users to handle multi-turn rejection flows.
-- =============================================================================

CREATE TYPE conversation_state AS ENUM (
    'AWAITING_REJECTION_REASON',  -- User said NO; waiting for 1/2/3 choice
    'AWAITING_CATEGORY_HINT'      -- User chose "wrong category"; waiting for their description
);

ALTER TABLE users
    ADD COLUMN conversation_state conversation_state;

COMMENT ON COLUMN users.conversation_state IS
    'Tracks mid-conversation state for multi-turn flows (e.g. rejection handling). NULL = no pending interaction.';
