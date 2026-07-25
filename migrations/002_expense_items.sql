-- Migration 002: itemized expenses (line items + assignments)
-- Prefer: python migrations/run_002_expense_items.py

ALTER TABLE expenses
    ADD COLUMN tax_tip_amount FLOAT NOT NULL DEFAULT 0 AFTER split_type;

CREATE TABLE IF NOT EXISTS expense_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    expense_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    price FLOAT NOT NULL,
    quantity FLOAT NOT NULL DEFAULT 1,
    CONSTRAINT fk_expense_items_expense
        FOREIGN KEY (expense_id) REFERENCES expenses(id) ON DELETE CASCADE,
    INDEX idx_expense_items_expense_id (expense_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS expense_item_assignments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    expense_item_id INT NOT NULL,
    user_id INT NOT NULL,
    CONSTRAINT uq_expense_item_assignments_item_user
        UNIQUE (expense_item_id, user_id),
    CONSTRAINT fk_expense_item_assignments_item
        FOREIGN KEY (expense_item_id) REFERENCES expense_items(id) ON DELETE CASCADE,
    CONSTRAINT fk_expense_item_assignments_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    INDEX idx_expense_item_assignments_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
