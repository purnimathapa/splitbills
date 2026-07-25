#!/usr/bin/env python3
"""Apply migration 003 (expense payment links) using DATABASE_URL from config."""

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
        raise ValueError(f"Unsupported DATABASE_URL for this script: {url}")
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def main():
    conn_params = parse_mysql_url(Config.SQLALCHEMY_DATABASE_URI)
    database = conn_params.pop("database")

    connection = pymysql.connect(
        database=database,
        charset="utf8mb4",
        autocommit=False,
        **conn_params,
    )

    try:
        with connection.cursor() as cursor:
            if not table_exists(cursor, database, "expense_payment_links"):
                cursor.execute(CREATE_TABLE)
                print("Created expense_payment_links table")
            else:
                print("expense_payment_links already exists — skipped")

        connection.commit()
        print("Migration 003 applied successfully.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
