#!/usr/bin/env python3
"""Apply migration 017 (analytics query indexes)."""

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


def index_exists(cursor, schema: str, table: str, index_name: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND INDEX_NAME = %s
        """,
        (schema, table, index_name),
    )
    return cursor.fetchone()[0] > 0


INDEXES = [
    ("expenses", "idx_expenses_trip_created", "CREATE INDEX idx_expenses_trip_created ON expenses (trip_id, created_at)"),
    ("expenses", "idx_expenses_paid_by_created", "CREATE INDEX idx_expenses_paid_by_created ON expenses (paid_by, created_at)"),
    ("expenses", "idx_expenses_created_at", "CREATE INDEX idx_expenses_created_at ON expenses (created_at)"),
    ("expenses", "idx_expenses_is_recurring", "CREATE INDEX idx_expenses_is_recurring ON expenses (is_recurring)"),
    (
        "expense_payment_links",
        "idx_payment_links_user_status",
        "CREATE INDEX idx_payment_links_user_status ON expense_payment_links (user_id, status)",
    ),
    (
        "expense_payment_links",
        "idx_payment_links_expense_status",
        "CREATE INDEX idx_payment_links_expense_status ON expense_payment_links (expense_id, status)",
    ),
]


def main():
    conn_params = parse_mysql_url(Config.SQLALCHEMY_DATABASE_URI)
    database = conn_params.pop("database")
    connection = pymysql.connect(
        database=database, charset="utf8mb4", autocommit=False, **conn_params
    )
    try:
        with connection.cursor() as cursor:
            for table, name, ddl in INDEXES:
                if index_exists(cursor, database, table, name):
                    print(f"{name} already exists — skipped")
                else:
                    cursor.execute(ddl)
                    print(f"Added {name}")
        connection.commit()
        print("Migration 017 applied successfully.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
