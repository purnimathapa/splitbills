#!/usr/bin/env python3
"""Apply migration 005 (payment_reminder_logs)."""

import re
import sys
from pathlib import Path

import pymysql
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local")

from config import Config  # noqa: E402


def parse_mysql_url(url: str):
    match = re.match(
        r"mysql\+pymysql://(?P<user>[^:]+):(?P<password>[^@]+)@(?P<host>[^:/]+)(?::(?P<port>\d+))?/(?P<database>.+)",
        url,
    )
    if not match:
        raise ValueError(f"Unsupported DATABASE_URL: {url}")
    groups = match.groupdict()
    return {
        "user": groups["user"],
        "password": groups["password"],
        "host": groups["host"],
        "port": int(groups["port"] or 3306),
        "database": groups["database"],
    }


def table_exists(cursor, schema: str, table: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """,
        (schema, table),
    )
    return cursor.fetchone()[0] > 0


CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS payment_reminder_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    payment_link_id INT NOT NULL,
    email_to VARCHAR(120) NOT NULL,
    sent_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_payment_reminder_logs_link
        FOREIGN KEY (payment_link_id) REFERENCES expense_payment_links(id) ON DELETE CASCADE,
    INDEX idx_payment_reminder_logs_link_id (payment_link_id),
    INDEX idx_payment_reminder_logs_sent_at (sent_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def main():
    conn_params = parse_mysql_url(Config.SQLALCHEMY_DATABASE_URI)
    database = conn_params.pop("database")
    connection = pymysql.connect(
        database=database, charset="utf8mb4", autocommit=False, **conn_params
    )
    try:
        with connection.cursor() as cursor:
            if not table_exists(cursor, database, "payment_reminder_logs"):
                cursor.execute(CREATE_TABLE)
                print("Created payment_reminder_logs table")
            else:
                print("payment_reminder_logs already exists — skipped")
        connection.commit()
        print("Migration 005 applied successfully.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
