-- Migration 015: recurring template linkage, occurrence date, optional end date
ALTER TABLE expenses
    ADD COLUMN recurrence_end_date DATE NULL AFTER next_occurrence_date,
    ADD COLUMN recurring_template_id INT NULL AFTER recurrence_end_date,
    ADD COLUMN recurrence_occurrence_date DATE NULL AFTER recurring_template_id;

ALTER TABLE expenses
    ADD CONSTRAINT fk_expense_recurring_template
        FOREIGN KEY (recurring_template_id) REFERENCES expenses(id);

CREATE UNIQUE INDEX uq_expense_recurring_occurrence
    ON expenses (recurring_template_id, recurrence_occurrence_date);
