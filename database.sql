CREATE DATABASE IF NOT EXISTS splitbills;

USE splitbills;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(120) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    profile_pic VARCHAR(255) DEFAULT 'default.png',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trips (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trip_name VARCHAR(150) NOT NULL,
    invite_code VARCHAR(10) UNIQUE,
    created_by INT,
    is_active BOOLEAN DEFAULT TRUE,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS trip_members (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trip_id INT,
    user_id INT,
    FOREIGN KEY (trip_id) REFERENCES trips(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS expenses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trip_id INT,
    paid_by INT,
    category VARCHAR(100),
    description VARCHAR(255),
    amount FLOAT,
    remarks VARCHAR(255),
    split_type VARCHAR(20) NOT NULL DEFAULT 'equal',
    tax_tip_amount FLOAT NOT NULL DEFAULT 0,
    receipt_image_url VARCHAR(512) NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (trip_id) REFERENCES trips(id),
    FOREIGN KEY (paid_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS expense_splits (
    id INT AUTO_INCREMENT PRIMARY KEY,
    expense_id INT NOT NULL,
    user_id INT NOT NULL,
    amount_owed FLOAT NOT NULL,
    percentage FLOAT NULL,
    shares FLOAT NULL,
    UNIQUE KEY uq_expense_splits_expense_user (expense_id, user_id),
    FOREIGN KEY (expense_id) REFERENCES expenses(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id),
    INDEX idx_expense_splits_expense_id (expense_id),
    INDEX idx_expense_splits_user_id (user_id)
);

CREATE TABLE IF NOT EXISTS expense_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    expense_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    price FLOAT NOT NULL,
    quantity FLOAT NOT NULL DEFAULT 1,
    FOREIGN KEY (expense_id) REFERENCES expenses(id) ON DELETE CASCADE,
    INDEX idx_expense_items_expense_id (expense_id)
);

CREATE TABLE IF NOT EXISTS expense_item_assignments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    expense_item_id INT NOT NULL,
    user_id INT NOT NULL,
    UNIQUE KEY uq_expense_item_assignments_item_user (expense_item_id, user_id),
    FOREIGN KEY (expense_item_id) REFERENCES expense_items(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id),
    INDEX idx_expense_item_assignments_user_id (user_id)
);

CREATE TABLE IF NOT EXISTS expense_payment_links (
    id INT AUTO_INCREMENT PRIMARY KEY,
    link_uuid VARCHAR(36) NOT NULL,
    expense_id INT NOT NULL,
    user_id INT NOT NULL,
    amount_owed FLOAT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    paid_at DATETIME NULL,
    payment_provider VARCHAR(30) NULL,
    khalti_pidx VARCHAR(80) NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_expense_payment_links_expense_user (expense_id, user_id),
    UNIQUE KEY uq_expense_payment_links_uuid (link_uuid),
    FOREIGN KEY (expense_id) REFERENCES expenses(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id),
    INDEX idx_expense_payment_links_expense_id (expense_id)
);

CREATE TABLE IF NOT EXISTS payment_reminder_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    payment_link_id INT NOT NULL,
    email_to VARCHAR(120) NOT NULL,
    sent_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (payment_link_id) REFERENCES expense_payment_links(id) ON DELETE CASCADE,
    INDEX idx_payment_reminder_logs_link_id (payment_link_id),
    INDEX idx_payment_reminder_logs_sent_at (sent_at)
);
