-- Migration 005: payment reminder send log
-- Prefer: python migrations/run_005_payment_reminder_logs.py

CREATE TABLE IF NOT EXISTS payment_reminder_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    payment_link_id INT NOT NULL,
    email_to VARCHAR(120) NOT NULL,
    sent_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_payment_reminder_logs_link
        FOREIGN KEY (payment_link_id) REFERENCES expense_payment_links(id) ON DELETE CASCADE,
    INDEX idx_payment_reminder_logs_link_id (payment_link_id),
    INDEX idx_payment_reminder_logs_sent_at (sent_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
