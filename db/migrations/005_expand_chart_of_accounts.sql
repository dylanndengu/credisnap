-- =============================================================================
-- CrediSnap: Expand Chart of Accounts
-- Version: 005
-- Adds commonly-used SA SME expense codes missing from the initial seed.
-- =============================================================================

INSERT INTO account_templates
    (code,   name,                                  account_type, normal_balance, parent_code, ifrs_line_item,          sort_order)
VALUES
('6200', 'IT and Software Subscriptions',           'EXPENSE', 'DEBIT', '6000', 'admin_expenses',             591),
('6210', 'Entertainment and Client Gifts',          'EXPENSE', 'DEBIT', '6000', 'entertainment',              592),  -- 50% VAT input limitation (VAT Act s17(2))
('6220', 'Training and Staff Development',          'EXPENSE', 'DEBIT', '6000', 'employee_costs',             593),
('6230', 'Cleaning and Pest Control',               'EXPENSE', 'DEBIT', '6000', 'admin_expenses',             594),
('6240', 'Security and Alarm',                      'EXPENSE', 'DEBIT', '6000', 'admin_expenses',             595),
('6250', 'Packaging and Consumables',               'EXPENSE', 'DEBIT', '6000', 'cost_of_sales',              596),
('6260', 'Courier and Postage',                     'EXPENSE', 'DEBIT', '6000', 'admin_expenses',             597),
('6270', 'Subscriptions and Memberships',           'EXPENSE', 'DEBIT', '6000', 'admin_expenses',             598),
('6280', 'Skills Development Levy',                 'EXPENSE', 'DEBIT', '6000', 'employee_costs',             600),  -- Payroll levy — VAT OP
('6290', 'COIDA / Workmen''s Compensation',         'EXPENSE', 'DEBIT', '6000', 'employee_costs',             601);  -- Payroll levy — VAT OP
