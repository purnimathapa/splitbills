-- Migration 017: analytics query indexes
CREATE INDEX idx_expenses_trip_created ON expenses (trip_id, created_at);
CREATE INDEX idx_expenses_paid_by_created ON expenses (paid_by, created_at);
CREATE INDEX idx_expenses_created_at ON expenses (created_at);
CREATE INDEX idx_expenses_is_recurring ON expenses (is_recurring);
CREATE INDEX idx_payment_links_user_status ON expense_payment_links (user_id, status);
CREATE INDEX idx_payment_links_expense_status ON expense_payment_links (expense_id, status);
