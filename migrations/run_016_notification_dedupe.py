#!/usr/bin/env python3
"""Apply migration 016 (notification dedupe_key and indexes)."""

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


def main():
    conn_params = parse_mysql_url(Config.SQLALCHEMY_DATABASE_URI)
    database = conn_params.pop("database")
    connection = pymysql.connect(
        database=database, charset="utf8mb4", autocommit=False, **conn_params
    )
    try:
        with connection.cursor() as cursor:
            if not column_exists(cursor, database, "notifications", "dedupe_key"):
                cursor.execute(
                    """
                    ALTER TABLE notifications
                    ADD COLUMN dedupe_key VARCHAR(128) NULL AFTER href
                    """
                )
                print("Added notifications.dedupe_key")
            else:
                print("dedupe_key already exists — skipped")

            if not index_exists(cursor, database, "notifications", "uq_notifications_user_dedupe"):
                cursor.execute(
                    """
                    CREATE UNIQUE INDEX uq_notifications_user_dedupe
                        ON notifications (user_id, dedupe_key)
                    """
                )
                print("Added uq_notifications_user_dedupe")
            else:
                print("uq_notifications_user_dedupe already exists — skipped")

            if not index_exists(cursor, database, "notifications", "idx_notifications_user_created"):
                cursor.execute(
                    """
                    CREATE INDEX idx_notifications_user_created
                        ON notifications (user_id, created_at)
                    """
                )
                print("Added idx_notifications_user_created")
            else:
                print("idx_notifications_user_created already exists — skipped")

        connection.commit()
        print("Migration 016 applied successfully.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
