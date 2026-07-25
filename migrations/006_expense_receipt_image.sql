-- Migration 006: expense receipt image URL
-- Prefer: python migrations/run_006_expense_receipt_image.py

ALTER TABLE expenses
    ADD COLUMN receipt_image_url VARCHAR(512) NULL AFTER tax_tip_amount;
