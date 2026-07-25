#!/usr/bin/env python3
"""Migration 012: nullable expense.trip_id + expense_participants for one-off splits."""

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


def column_nullable(cursor, schema: str, table: str, column: str) -> bool | None:
    cursor.execute(
        """
        SELECT IS_NULLABLE FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (schema, table, column),
    )
    row = cursor.fetchone()
    return None if not row else row[0] == "YES"


def table_exists(cursor, schema: str, table: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*) FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """,
        (schema, table),
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
            nullable = column_nullable(cursor, database, "expenses", "trip_id")
            if nullable is False:
                cursor.execute(
                    "ALTER TABLE expenses MODIFY COLUMN trip_id INT NULL"
                )
                print("Made expenses.trip_id nullable")

            if not table_exists(cursor, database, "expense_participants"):
                cursor.execute(
                    """
                    CREATE TABLE expense_participants (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        expense_id INT NOT NULL,
                        user_id INT NOT NULL,
                        CONSTRAINT fk_exp_part_expense
                            FOREIGN KEY (expense_id) REFERENCES expenses(id) ON DELETE CASCADE,
                        CONSTRAINT fk_exp_part_user
                            FOREIGN KEY (user_id) REFERENCES users(id),
                        CONSTRAINT uq_expense_participants_expense_user
                            UNIQUE (expense_id, user_id)
                    )
                    """
                )
                print("Created expense_participants table")

        connection.commit()
        print("Migration 012 applied successfully.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
