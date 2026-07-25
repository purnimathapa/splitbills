#!/usr/bin/env python3
"""Apply migration 001 (expense split types) using DATABASE_URL from config."""

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


ADD_SPLIT_TYPE = """
ALTER TABLE expenses
    ADD COLUMN split_type VARCHAR(20) NOT NULL DEFAULT 'equal' AFTER remarks
"""

CREATE_EXPENSE_SPLITS = """
CREATE TABLE IF NOT EXISTS expense_splits (
    id INT AUTO_INCREMENT PRIMARY KEY,
    expense_id INT NOT NULL,
    user_id INT NOT NULL,
    amount_owed FLOAT NOT NULL,
    percentage FLOAT NULL COMMENT 'Input weight 0-100 when split_type is percentage',
    shares FLOAT NULL COMMENT 'Input share count when split_type is shares',
    CONSTRAINT uq_expense_splits_expense_user UNIQUE (expense_id, user_id),
    CONSTRAINT fk_expense_splits_expense
        FOREIGN KEY (expense_id) REFERENCES expenses(id) ON DELETE CASCADE,
    CONSTRAINT fk_expense_splits_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    INDEX idx_expense_splits_expense_id (expense_id),
    INDEX idx_expense_splits_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

BACKFILL_SPLITS = """
INSERT INTO expense_splits (expense_id, user_id, amount_owed, percentage, shares)
SELECT
    e.id AS expense_id,
    tm.user_id,
    ROUND(e.amount / NULLIF(mc.member_count, 0), 2) AS amount_owed,
    NULL AS percentage,
    NULL AS shares
FROM expenses e
INNER JOIN trip_members tm ON tm.trip_id = e.trip_id
INNER JOIN (
    SELECT trip_id, COUNT(*) AS member_count
    FROM trip_members
    GROUP BY trip_id
) mc ON mc.trip_id = e.trip_id
WHERE e.amount IS NOT NULL
  AND e.amount > 0
  AND mc.member_count > 0
  AND NOT EXISTS (
      SELECT 1 FROM expense_splits es WHERE es.expense_id = e.id
  )
"""

UPDATE_SPLIT_TYPE = """
UPDATE expenses
SET split_type = 'equal'
WHERE split_type IS NULL OR split_type = ''
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
            if not column_exists(cursor, database, "expenses", "split_type"):
                cursor.execute(ADD_SPLIT_TYPE)
                print("Added expenses.split_type")
            else:
                print("expenses.split_type already exists — skipped")

            if not table_exists(cursor, database, "expense_splits"):
                cursor.execute(CREATE_EXPENSE_SPLITS)
                print("Created expense_splits table")
            else:
                print("expense_splits already exists — skipped")

            cursor.execute(UPDATE_SPLIT_TYPE)
            cursor.execute(BACKFILL_SPLITS)
            print(f"Backfill complete ({cursor.rowcount} split rows inserted)")

        connection.commit()
        print("Migration 001 applied successfully.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
