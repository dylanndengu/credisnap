-- =============================================================================
-- CrediSnap: Business profile fields
-- Version: 014
-- Captures richer SME context during onboarding for analytics and better
-- LLM categorisation (knowing the business type helps with ambiguous items).
-- =============================================================================

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS business_type  VARCHAR(50),
    ADD COLUMN IF NOT EXISTS province       VARCHAR(50),
    ADD COLUMN IF NOT EXISTS latitude       DECIMAL(9, 6),
    ADD COLUMN IF NOT EXISTS longitude      DECIMAL(9, 6);

COMMENT ON COLUMN users.business_type IS
    'Sector the SME operates in — captured during onboarding. '
    'E.g. Retail, Food & Catering, Professional Services, Construction.';

COMMENT ON COLUMN users.province IS
    'SA province — captured during onboarding via numbered menu.';

COMMENT ON COLUMN users.latitude IS
    'GPS latitude — set if the user voluntarily shares their WhatsApp location.';

COMMENT ON COLUMN users.longitude IS
    'GPS longitude — set if the user voluntarily shares their WhatsApp location.';
