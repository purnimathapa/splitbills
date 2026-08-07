-- Migration 013: FLOAT → DECIMAL for all monetary columns
-- Prefer: python migrations/run_013_money_decimal.py

ALTER TABLE expenses
    MODIFY COLUMN amount DECIMAL(12,2) NULL,
    MODIFY COLUMN tax_tip_amount DECIMAL(12,2) NOT NULL DEFAULT 0;

ALTER TABLE expense_splits
    MODIFY COLUMN amount_owed DECIMAL(12,2) NOT NULL,
    MODIFY COLUMN percentage DECIMAL(8,4) NULL,
    MODIFY COLUMN shares DECIMAL(8,4) NULL;

ALTER TABLE expense_items
    MODIFY COLUMN price DECIMAL(12,2) NOT NULL,
    MODIFY COLUMN quantity DECIMAL(10,4) NOT NULL DEFAULT 1;

ALTER TABLE expense_payment_links
    MODIFY COLUMN amount_owed DECIMAL(12,2) NOT NULL;
