-- =============================================================================
-- CrediSnap: Onboarding Step Tracking
-- Version: 004
-- Adds onboarding_step column to users to drive the WhatsApp onboarding flow.
-- =============================================================================

CREATE TYPE onboarding_step AS ENUM (
    'BUSINESS_NAME',   -- Awaiting business name
    'TAX_REF',         -- Awaiting SARS income tax reference (skippable)
    'DONE'             -- Onboarding complete
);

ALTER TABLE users
    ADD COLUMN onboarding_step onboarding_step;

-- NULL means the user was created before this migration (treat as DONE),
-- or consent has not yet been granted.
-- Set to 'BUSINESS_NAME' when consent is first granted.

COMMENT ON COLUMN users.onboarding_step IS
    'Tracks progress through the WhatsApp onboarding flow. NULL = pre-consent or legacy user. DONE = onboarding complete.';
