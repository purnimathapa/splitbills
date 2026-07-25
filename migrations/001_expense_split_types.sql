-- Migration 001: expense split types + expense_splits
-- Prefer: python migrations/run_001_expense_split_types.py
-- Or run these statements manually against splitbills (MySQL).

ALTER TABLE expenses
    ADD COLUMN split_type VARCHAR(20) NOT NULL DEFAULT 'equal' AFTER remarks;

CREATE TABLE IF NOT EXISTS expense_splits (
    id INT AUTO_INCREMENT PRIMARY KEY,
    expense_id INT NOT NULL,
    user_id INT NOT NULL,
    amount_owed FLOAT NOT NULL,
    percentage FLOAT NULL COMMENT 'Input weight 0-100 when split_type is percentage',
    shares FLOAT NULL COMMENT 'Input share count when split_type is shares',
    CONSTRAINT uq_expense_splits_expense_user UNIQUE (expense_id, user_id),
    CONSTRAINT fk_expense_splits_expense
        FOREIGN KEY (expense_id) REFERENCES expenses(id) ON DELETE CASCADE,
    CONSTRAINT fk_expense_splits_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    INDEX idx_expense_splits_expense_id (expense_id),
    INDEX idx_expense_splits_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

UPDATE expenses
SET split_type = 'equal'
WHERE split_type IS NULL OR split_type = '';

INSERT INTO expense_splits (expense_id, user_id, amount_owed, percentage, shares)
SELECT
    e.id AS expense_id,
    tm.user_id,
    ROUND(e.amount / NULLIF(mc.member_count, 0), 2) AS amount_owed,
    NULL AS percentage,
    NULL AS shares
FROM expenses e
INNER JOIN trip_members tm ON tm.trip_id = e.trip_id
INNER JOIN (
    SELECT trip_id, COUNT(*) AS member_count
    FROM trip_members
    GROUP BY trip_id
) mc ON mc.trip_id = e.trip_id
WHERE e.amount IS NOT NULL
  AND e.amount > 0
  AND mc.member_count > 0
  AND NOT EXISTS (
      SELECT 1 FROM expense_splits es WHERE es.expense_id = e.id
  );
