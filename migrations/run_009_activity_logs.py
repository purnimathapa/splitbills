#!/usr/bin/env python3
"""Apply migration 009 (activity_logs table)."""

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


DDL = """
CREATE TABLE activity_logs (
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
)
"""


def main():
    conn_params = parse_mysql_url(Config.SQLALCHEMY_DATABASE_URI)
    database = conn_params.pop("database")
    connection = pymysql.connect(
        database=database, charset="utf8mb4", autocommit=False, **conn_params
    )
    try:
        with connection.cursor() as cursor:
            if table_exists(cursor, database, "activity_logs"):
                print("activity_logs already exists — skipped")
            else:
                cursor.execute(DDL)
                print("Created activity_logs")
        connection.commit()
        print("Migration 009 applied successfully.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
