-- Activity feed (009)
CREATE TABLE IF NOT EXISTS activity_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trip_id INT NULL,
    actor_user_id INT NOT NULL,
    action_type VARCHAR(40) NOT NULL,
    description VARCHAR(512) NOT NULL,
    related_expense_id INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_activity_trip FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE SET NULL,
    CONSTRAINT fk_activity_actor FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_activity_expense FOREIGN KEY (related_expense_id) REFERENCES expenses(id) ON DELETE SET NULL,
    INDEX idx_activity_trip_created (trip_id, created_at DESC),
    INDEX idx_activity_created (created_at DESC)
);
