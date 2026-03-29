-- =============================================================================
-- CrediSnap: Standard SA SME Chart of Accounts (Seed Template)
-- =============================================================================
-- This is a TEMPLATE — copied per user at onboarding via application code.
-- Aligned with IFRS for SMEs (IASB 2015) and common SA accounting practice.
-- Account codes follow the 1000/2000/3000/4000/5000/6000 convention.
-- =============================================================================

-- Usage: INSERT INTO accounts (user_id, code, name, account_type, normal_balance, ifrs_line_item)
-- SELECT :user_id, code, name, account_type, normal_balance, ifrs_line_item FROM account_templates;

CREATE TABLE account_templates (
    code            VARCHAR(20)         NOT NULL PRIMARY KEY,
    name            VARCHAR(255)        NOT NULL,
    account_type    account_type        NOT NULL,
    normal_balance  normal_balance_type NOT NULL,
    parent_code     VARCHAR(20),
    ifrs_line_item  VARCHAR(100),
    sort_order      SMALLINT            NOT NULL DEFAULT 0
);

INSERT INTO account_templates
    (code,   name,                                  account_type, normal_balance, parent_code, ifrs_line_item,            sort_order)
VALUES
-- ======================================================
-- ASSETS (1000–1999)
-- ======================================================
('1000', 'Current Assets',                          'ASSET', 'DEBIT', NULL,   'current_assets',               10),
('1010', 'Cash and Cash Equivalents',               'ASSET', 'DEBIT', '1000', 'cash_and_equivalents',         11),
('1020', 'Business Bank Account',                   'ASSET', 'DEBIT', '1010', 'cash_and_equivalents',         12),
('1030', 'Petty Cash',                              'ASSET', 'DEBIT', '1010', 'cash_and_equivalents',         13),
('1100', 'Accounts Receivable',                     'ASSET', 'DEBIT', '1000', 'trade_receivables',            20),
('1110', 'Trade Debtors',                           'ASSET', 'DEBIT', '1100', 'trade_receivables',            21),
('1120', 'Allowance for Doubtful Debts',            'ASSET', 'CREDIT','1100', 'trade_receivables',            22),  -- Contra asset
('1200', 'VAT Input Account',                       'ASSET', 'DEBIT', '1000', 'other_current_assets',         30),  -- SARS VAT201
('1300', 'Inventory',                               'ASSET', 'DEBIT', '1000', 'inventories',                  40),
('1400', 'Prepaid Expenses',                        'ASSET', 'DEBIT', '1000', 'other_current_assets',         50),
('1500', 'Non-Current Assets',                      'ASSET', 'DEBIT', NULL,   'non_current_assets',           60),
('1510', 'Property, Plant and Equipment (Cost)',    'ASSET', 'DEBIT', '1500', 'ppe_net',                      61),
('1520', 'Accumulated Depreciation',                'ASSET', 'CREDIT','1500', 'ppe_net',                      62),  -- Contra asset
('1530', 'Right-of-Use Assets',                     'ASSET', 'DEBIT', '1500', 'right_of_use_assets',          63),  -- IFRS 16

-- ======================================================
-- LIABILITIES (2000–2999)
-- ======================================================
('2000', 'Current Liabilities',                     'LIABILITY', 'CREDIT', NULL,   'current_liabilities',      110),
('2010', 'Accounts Payable',                        'LIABILITY', 'CREDIT', '2000', 'trade_payables',           111),
('2020', 'Trade Creditors',                         'LIABILITY', 'CREDIT', '2010', 'trade_payables',           112),
('2100', 'VAT Output Account',                      'LIABILITY', 'CREDIT', '2000', 'tax_payable',              120),  -- SARS VAT201
('2110', 'PAYE Payable',                            'LIABILITY', 'CREDIT', '2000', 'tax_payable',              121),  -- SARS EMP201
('2120', 'UIF Payable',                             'LIABILITY', 'CREDIT', '2000', 'tax_payable',              122),  -- UIF Act
('2200', 'Short-Term Borrowings',                   'LIABILITY', 'CREDIT', '2000', 'short_term_borrowings',    130),
('2210', 'Business Overdraft',                      'LIABILITY', 'CREDIT', '2200', 'short_term_borrowings',    131),
('2300', 'Accrued Liabilities',                     'LIABILITY', 'CREDIT', '2000', 'accrued_liabilities',      140),
('2310', 'Accrued Salaries',                        'LIABILITY', 'CREDIT', '2300', 'accrued_liabilities',      141),
('2400', 'Non-Current Liabilities',                 'LIABILITY', 'CREDIT', NULL,   'non_current_liabilities',  150),
('2410', 'Long-Term Borrowings',                    'LIABILITY', 'CREDIT', '2400', 'long_term_borrowings',     151),
('2420', 'Lease Liabilities',                       'LIABILITY', 'CREDIT', '2400', 'lease_liabilities',        152),  -- IFRS 16

-- ======================================================
-- EQUITY (3000–3999)
-- ======================================================
('3000', 'Equity',                                  'EQUITY', 'CREDIT', NULL,   'total_equity',               210),
('3010', 'Owners Capital / Share Capital',          'EQUITY', 'CREDIT', '3000', 'share_capital',              211),
('3020', 'Retained Earnings',                       'EQUITY', 'CREDIT', '3000', 'retained_earnings',          212),
('3030', 'Current Year Profit / (Loss)',            'EQUITY', 'CREDIT', '3000', 'current_year_profit',        213),
('3040', 'Drawings',                                'EQUITY', 'DEBIT',  '3000', 'drawings',                   214),  -- Contra equity

-- ======================================================
-- REVENUE (4000–4999)
-- ======================================================
('4000', 'Revenue',                                 'REVENUE', 'CREDIT', NULL,   'revenue',                   310),
('4010', 'Sales — Products',                        'REVENUE', 'CREDIT', '4000', 'revenue',                   311),
('4020', 'Sales — Services',                        'REVENUE', 'CREDIT', '4000', 'revenue',                   312),
('4030', 'Other Income',                            'REVENUE', 'CREDIT', '4000', 'other_income',              320),
('4040', 'Interest Income',                         'REVENUE', 'CREDIT', '4000', 'finance_income',            330),

-- ======================================================
-- COST OF SALES (5000–5999)
-- ======================================================
('5000', 'Cost of Sales',                           'EXPENSE', 'DEBIT', NULL,   'cost_of_sales',              410),
('5010', 'Purchases — Goods for Resale',            'EXPENSE', 'DEBIT', '5000', 'cost_of_sales',              411),
('5020', 'Direct Labour',                           'EXPENSE', 'DEBIT', '5000', 'cost_of_sales',              412),
('5030', 'Freight Inwards',                         'EXPENSE', 'DEBIT', '5000', 'cost_of_sales',              413),

-- ======================================================
-- OPERATING EXPENSES (6000–6999)
-- ======================================================
('6000', 'Operating Expenses',                      'EXPENSE', 'DEBIT', NULL,   'operating_expenses',         510),
('6010', 'Salaries and Wages',                      'EXPENSE', 'DEBIT', '6000', 'employee_costs',             511),
('6020', 'Employer UIF Contribution',               'EXPENSE', 'DEBIT', '6000', 'employee_costs',             512),
('6030', 'Rent Expense',                            'EXPENSE', 'DEBIT', '6000', 'rent_expense',               520),
('6040', 'Utilities — Electricity',                 'EXPENSE', 'DEBIT', '6000', 'utilities',                  521),
('6050', 'Utilities — Water',                       'EXPENSE', 'DEBIT', '6000', 'utilities',                  522),
('6060', 'Telephone and Internet',                  'EXPENSE', 'DEBIT', '6000', 'communication',              530),
('6070', 'Motor Vehicle Expenses',                  'EXPENSE', 'DEBIT', '6000', 'motor_vehicle',              540),
('6080', 'Fuel and Oil',                            'EXPENSE', 'DEBIT', '6000', 'motor_vehicle',              541),
('6090', 'Repairs and Maintenance',                 'EXPENSE', 'DEBIT', '6000', 'repairs_maintenance',        550),
('6100', 'Stationery and Printing',                 'EXPENSE', 'DEBIT', '6000', 'admin_expenses',             560),
('6110', 'Bank Charges',                            'EXPENSE', 'DEBIT', '6000', 'bank_charges',               561),
('6120', 'Professional Fees — Accounting',          'EXPENSE', 'DEBIT', '6000', 'professional_fees',          562),
('6130', 'Professional Fees — Legal',               'EXPENSE', 'DEBIT', '6000', 'professional_fees',          563),
('6140', 'Insurance',                               'EXPENSE', 'DEBIT', '6000', 'insurance',                  570),
('6150', 'Depreciation',                            'EXPENSE', 'DEBIT', '6000', 'depreciation',               571),
('6160', 'Advertising and Marketing',               'EXPENSE', 'DEBIT', '6000', 'marketing',                  580),
('6170', 'Travel and Accommodation',                'EXPENSE', 'DEBIT', '6000', 'travel',                     581),
('6180', 'Interest Expense',                        'EXPENSE', 'DEBIT', '6000', 'finance_costs',              590),
('6190', 'Sundry Expenses',                         'EXPENSE', 'DEBIT', '6000', 'sundry_expenses',            599);
