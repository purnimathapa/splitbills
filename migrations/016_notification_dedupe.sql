-- Migration 016: notification dedupe keys and query indexes
ALTER TABLE notifications
    ADD COLUMN dedupe_key VARCHAR(128) NULL AFTER href;

CREATE UNIQUE INDEX uq_notifications_user_dedupe
    ON notifications (user_id, dedupe_key);

CREATE INDEX idx_notifications_user_created
    ON notifications (user_id, created_at);
