#!/usr/bin/env python3
"""Apply migration 002 (expense line items) using DATABASE_URL from config."""

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


def column_exists(cursor, schema: str, table: str, column: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (schema, table, column),
    )
    return cursor.fetchone()[0] > 0


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


ADD_TAX_TIP = """
ALTER TABLE expenses
    ADD COLUMN tax_tip_amount FLOAT NOT NULL DEFAULT 0 AFTER split_type
"""

CREATE_EXPENSE_ITEMS = """
CREATE TABLE IF NOT EXISTS expense_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    expense_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    price FLOAT NOT NULL,
    quantity FLOAT NOT NULL DEFAULT 1,
    CONSTRAINT fk_expense_items_expense
        FOREIGN KEY (expense_id) REFERENCES expenses(id) ON DELETE CASCADE,
    INDEX idx_expense_items_expense_id (expense_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

CREATE_ASSIGNMENTS = """
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
            if not column_exists(cursor, database, "expenses", "tax_tip_amount"):
                cursor.execute(ADD_TAX_TIP)
                print("Added expenses.tax_tip_amount")
            else:
                print("expenses.tax_tip_amount already exists — skipped")

            if not table_exists(cursor, database, "expense_items"):
                cursor.execute(CREATE_EXPENSE_ITEMS)
                print("Created expense_items table")
            else:
                print("expense_items already exists — skipped")

            if not table_exists(cursor, database, "expense_item_assignments"):
                cursor.execute(CREATE_ASSIGNMENTS)
                print("Created expense_item_assignments table")
            else:
                print("expense_item_assignments already exists — skipped")

        connection.commit()
        print("Migration 002 applied successfully.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
