#!/usr/bin/env python3
"""Apply migration 011 (self-service item claims)."""

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


EXPENSE_COLS = [
    ("self_service_items", "ALTER TABLE expenses ADD COLUMN self_service_items TINYINT(1) NOT NULL DEFAULT 0"),
    ("claims_finalized_at", "ALTER TABLE expenses ADD COLUMN claims_finalized_at DATETIME NULL"),
]
LINK_COLS = [
    ("items_claimed_at", "ALTER TABLE expense_payment_links ADD COLUMN items_claimed_at DATETIME NULL"),
]


def main():
    conn_params = parse_mysql_url(Config.SQLALCHEMY_DATABASE_URI)
    database = conn_params.pop("database")
    connection = pymysql.connect(
        database=database, charset="utf8mb4", autocommit=False, **conn_params
    )
    try:
        with connection.cursor() as cursor:
            for name, ddl in EXPENSE_COLS:
                if not column_exists(cursor, database, "expenses", name):
                    cursor.execute(ddl)
                    print(f"Added expenses.{name}")
            for name, ddl in LINK_COLS:
                if not column_exists(cursor, database, "expense_payment_links", name):
                    cursor.execute(ddl)
                    print(f"Added expense_payment_links.{name}")
        connection.commit()
        print("Migration 011 applied successfully.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
