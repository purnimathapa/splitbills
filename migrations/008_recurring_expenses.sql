-- Recurring expense templates (008)
ALTER TABLE expenses
    ADD COLUMN is_recurring TINYINT(1) NOT NULL DEFAULT 0 AFTER created_at,
    ADD COLUMN recurrence_interval VARCHAR(20) NULL AFTER is_recurring,
    ADD COLUMN next_occurrence_date DATE NULL AFTER recurrence_interval;
