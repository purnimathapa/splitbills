#!/usr/bin/env python3
"""Migration 013: FLOAT → DECIMAL for persisted monetary columns."""

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

STATEMENTS = [
    (
        "expenses.amount",
        "ALTER TABLE expenses MODIFY COLUMN amount DECIMAL(12,2) NULL",
    ),
    (
        "expenses.tax_tip_amount",
        "ALTER TABLE expenses MODIFY COLUMN tax_tip_amount DECIMAL(12,2) NOT NULL DEFAULT 0",
    ),
    (
        "expense_splits.amount_owed",
        "ALTER TABLE expense_splits MODIFY COLUMN amount_owed DECIMAL(12,2) NOT NULL",
    ),
    (
        "expense_splits.percentage",
        "ALTER TABLE expense_splits MODIFY COLUMN percentage DECIMAL(8,4) NULL",
    ),
    (
        "expense_splits.shares",
        "ALTER TABLE expense_splits MODIFY COLUMN shares DECIMAL(8,4) NULL",
    ),
    (
        "expense_items.price",
        "ALTER TABLE expense_items MODIFY COLUMN price DECIMAL(12,2) NOT NULL",
    ),
    (
        "expense_items.quantity",
        "ALTER TABLE expense_items MODIFY COLUMN quantity DECIMAL(10,4) NOT NULL DEFAULT 1",
    ),
    (
        "expense_payment_links.amount_owed",
        "ALTER TABLE expense_payment_links MODIFY COLUMN amount_owed DECIMAL(12,2) NOT NULL",
    ),
]


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


def column_type(cursor, schema: str, table: str, column: str) -> str | None:
    cursor.execute(
        """
        SELECT DATA_TYPE FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (schema, table, column),
    )
    row = cursor.fetchone()
    return None if not row else row[0].lower()


def main():
    conn_params = parse_mysql_url(Config.SQLALCHEMY_DATABASE_URI)
    database = conn_params.pop("database")
    connection = pymysql.connect(
        database=database, charset="utf8mb4", autocommit=False, **conn_params
    )
    try:
        with connection.cursor() as cursor:
            for label, statement in STATEMENTS:
                table, column = label.split(".")
                current = column_type(cursor, database, table, column)
                if current == "decimal":
                    print(f"Skip {label} (already DECIMAL)")
                    continue
                cursor.execute(statement)
                print(f"Updated {label} → DECIMAL")
        connection.commit()
        print("Migration 013 applied successfully.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
