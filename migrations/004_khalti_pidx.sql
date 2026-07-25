-- Migration 004: Khalti session id on payment links
-- Prefer: python migrations/run_004_khalti_pidx.py

ALTER TABLE expense_payment_links
    ADD COLUMN khalti_pidx VARCHAR(80) NULL AFTER payment_provider;
