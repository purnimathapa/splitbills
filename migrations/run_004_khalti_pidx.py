#!/usr/bin/env python3
"""Apply migration 004 (khalti_pidx column)."""

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


ADD_COLUMN = """
ALTER TABLE expense_payment_links
    ADD COLUMN khalti_pidx VARCHAR(80) NULL AFTER payment_provider
"""


def main():
    conn_params = parse_mysql_url(Config.SQLALCHEMY_DATABASE_URI)
    database = conn_params.pop("database")
    connection = pymysql.connect(
        database=database, charset="utf8mb4", autocommit=False, **conn_params
    )
    try:
        with connection.cursor() as cursor:
            if not column_exists(cursor, database, "expense_payment_links", "khalti_pidx"):
                cursor.execute(ADD_COLUMN)
                print("Added expense_payment_links.khalti_pidx")
            else:
                print("khalti_pidx already exists — skipped")
        connection.commit()
        print("Migration 004 applied successfully.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
