-- =============================================================================
-- CrediSnap: Expand Revenue Chart of Accounts
-- Version: 012
-- Adds 7 revenue codes covering common SA SME income types not captured by
-- the original 4010–4040 range.
-- =============================================================================

-- 1. Add to the template (used for all new users going forward)
INSERT INTO account_templates
    (code,   name,                              account_type, normal_balance, parent_code, ifrs_line_item,  sort_order)
VALUES
('4050', 'Consulting and Professional Fees',   'REVENUE', 'CREDIT', '4000', 'revenue',   313),
('4060', 'Commission and Agency Income',        'REVENUE', 'CREDIT', '4000', 'revenue',   314),
('4070', 'Rental Income',                       'REVENUE', 'CREDIT', '4000', 'other_income', 321),
('4080', 'Catering and Food Sales',             'REVENUE', 'CREDIT', '4000', 'revenue',   315),
('4090', 'Contract and Project Income',         'REVENUE', 'CREDIT', '4000', 'revenue',   316),
('4100', 'Maintenance and Repair Services',     'REVENUE', 'CREDIT', '4000', 'revenue',   317),
('4110', 'Freight and Delivery Income',         'REVENUE', 'CREDIT', '4000', 'revenue',   318)
ON CONFLICT (code) DO NOTHING;

-- 2. Seed into every existing user's accounts (mirrors the onboarding INSERT)
INSERT INTO accounts (user_id, code, name, account_type, normal_balance, parent_id, ifrs_line_item)
SELECT
    u.id,
    t.code,
    t.name,
    t.account_type,
    t.normal_balance,
    NULL,
    t.ifrs_line_item
FROM account_templates t
CROSS JOIN users u
WHERE t.code IN ('4050','4060','4070','4080','4090','4100','4110')
ON CONFLICT (user_id, code) DO NOTHING;
