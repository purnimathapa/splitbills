#!/usr/bin/env python3
"""Migration 014: add trip_members.role and backfill owners."""

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


def main():
    conn_params = parse_mysql_url(Config.SQLALCHEMY_DATABASE_URI)
    database = conn_params.pop("database")
    connection = pymysql.connect(
        database=database, charset="utf8mb4", autocommit=False, **conn_params
    )
    try:
        with connection.cursor() as cursor:
            if not column_exists(cursor, database, "trip_members", "role"):
                cursor.execute(
                    "ALTER TABLE trip_members "
                    "ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'member'"
                )
                print("Added trip_members.role")

            cursor.execute(
                """
                UPDATE trip_members tm
                INNER JOIN trips t ON t.id = tm.trip_id
                SET tm.role = 'owner'
                WHERE tm.user_id = t.created_by
                  AND tm.role <> 'owner'
                """
            )
            print(f"Set creator as owner ({cursor.rowcount} rows)")

            cursor.execute(
                """
                SELECT DISTINCT tm.trip_id
                FROM trip_members tm
                LEFT JOIN trip_members owners
                    ON owners.trip_id = tm.trip_id AND owners.role = 'owner'
                WHERE owners.id IS NULL
                """
            )
            trip_ids = [row[0] for row in cursor.fetchall()]
            for trip_id in trip_ids:
                cursor.execute(
                    """
                    SELECT id FROM trip_members
                    WHERE trip_id = %s
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (trip_id,),
                )
                row = cursor.fetchone()
                if row:
                    cursor.execute(
                        "UPDATE trip_members SET role = 'owner' WHERE id = %s",
                        (row[0],),
                    )
            if trip_ids:
                print(f"Backfilled owner for {len(trip_ids)} groups without one")

        connection.commit()
        print("Migration 014 applied successfully.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
