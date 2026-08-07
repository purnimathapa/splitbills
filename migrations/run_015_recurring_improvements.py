#!/usr/bin/env python3
"""Apply migration 015 (recurring template linkage and end date)."""

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


COLUMNS = [
    (
        "recurrence_end_date",
        "ALTER TABLE expenses ADD COLUMN recurrence_end_date DATE NULL AFTER next_occurrence_date",
    ),
    (
        "recurring_template_id",
        "ALTER TABLE expenses ADD COLUMN recurring_template_id INT NULL AFTER recurrence_end_date",
    ),
    (
        "recurrence_occurrence_date",
        "ALTER TABLE expenses ADD COLUMN recurrence_occurrence_date DATE NULL AFTER recurring_template_id",
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
            for name, ddl in COLUMNS:
                if not column_exists(cursor, database, "expenses", name):
                    cursor.execute(ddl)
                    print(f"Added expenses.{name}")
                else:
                    print(f"{name} already exists — skipped")

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.TABLE_CONSTRAINTS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = 'expenses'
                  AND CONSTRAINT_NAME = 'fk_expense_recurring_template'
                """,
                (database,),
            )
            if cursor.fetchone()[0] == 0:
                cursor.execute(
                    """
                    ALTER TABLE expenses
                    ADD CONSTRAINT fk_expense_recurring_template
                        FOREIGN KEY (recurring_template_id) REFERENCES expenses(id)
                    """
                )
                print("Added fk_expense_recurring_template")
            else:
                print("fk_expense_recurring_template already exists — skipped")

            if not index_exists(cursor, database, "expenses", "uq_expense_recurring_occurrence"):
                cursor.execute(
                    """
                    CREATE UNIQUE INDEX uq_expense_recurring_occurrence
                        ON expenses (recurring_template_id, recurrence_occurrence_date)
                    """
                )
                print("Added uq_expense_recurring_occurrence")
            else:
                print("uq_expense_recurring_occurrence already exists — skipped")

        connection.commit()
        print("Migration 015 applied successfully.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
