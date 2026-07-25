-- Migration 003: guest payment links
-- Prefer: python migrations/run_003_expense_payment_links.py

CREATE TABLE IF NOT EXISTS expense_payment_links (
    id INT AUTO_INCREMENT PRIMARY KEY,
    link_uuid VARCHAR(36) NOT NULL,
    expense_id INT NOT NULL,
    user_id INT NOT NULL,
    amount_owed FLOAT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    paid_at DATETIME NULL,
    payment_provider VARCHAR(30) NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_expense_payment_links_expense_user UNIQUE (expense_id, user_id),
    CONSTRAINT uq_expense_payment_links_uuid UNIQUE (link_uuid),
    CONSTRAINT fk_expense_payment_links_expense
        FOREIGN KEY (expense_id) REFERENCES expenses(id) ON DELETE CASCADE,
    CONSTRAINT fk_expense_payment_links_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    INDEX idx_expense_payment_links_expense_id (expense_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
