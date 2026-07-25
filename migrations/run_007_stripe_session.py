"""Add stripe_checkout_session_id to expense_payment_links."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app import app, db
from sqlalchemy import inspect, text


def column_exists(inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def main():
    with app.app_context():
        inspector = inspect(db.engine)
        if not column_exists(inspector, "expense_payment_links", "stripe_checkout_session_id"):
            db.session.execute(
                text(
                    """
                    ALTER TABLE expense_payment_links
                    ADD COLUMN stripe_checkout_session_id VARCHAR(255) NULL
                    AFTER khalti_pidx
                    """
                )
            )
            db.session.commit()
            print("Added expense_payment_links.stripe_checkout_session_id")
        else:
            print("stripe_checkout_session_id already exists — skipped")


if __name__ == "__main__":
    main()
