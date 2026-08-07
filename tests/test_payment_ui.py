"""Tests for payment page presentation helpers."""

import unittest
from decimal import Decimal
from types import SimpleNamespace

from models import PAYMENT_STATUS_PAID, PAYMENT_STATUS_PENDING
from services.payment_ui import (
    STATE_EXPIRED,
    STATE_FAILED,
    STATE_PENDING,
    STATE_PROCESSING,
    STATE_SUCCESS,
    build_payment_page_context,
    user_safe_checkout_error,
    user_safe_settlement_error,
)


class PaymentUiTests(unittest.TestCase):
    def test_safe_messages_hide_internals(self):
        self.assertNotIn("pidx", user_safe_settlement_error("pidx invalid"))
        self.assertNotIn("secret", user_safe_checkout_error(Exception("secret key leak")))

    def test_expired_invalid_link(self):
        ctx = build_payment_page_context(link=None, invalid=True)
        self.assertEqual(ctx["payment_state"], STATE_EXPIRED)
        self.assertFalse(ctx["show_payment_form"])

    def test_success_when_paid(self):
        link = SimpleNamespace(
            status=PAYMENT_STATUS_PAID,
            amount_owed=Decimal("850"),
            payment_provider="khalti",
            paid_at=None,
            expense=SimpleNamespace(description="Dinner"),
            khalti_pidx=None,
            stripe_checkout_session_id=None,
        )
        ctx = build_payment_page_context(link=link, payer_name="Ram")
        self.assertEqual(ctx["payment_state"], STATE_SUCCESS)
        self.assertIn("Payment successful", ctx["payment_title"])

    def test_failed_state(self):
        link = SimpleNamespace(
            status=PAYMENT_STATUS_PENDING,
            amount_owed=Decimal("50"),
            payment_provider=None,
            paid_at=None,
            expense=SimpleNamespace(description="Taxi"),
            khalti_pidx=None,
            stripe_checkout_session_id=None,
        )
        ctx = build_payment_page_context(link=link, payment_failed=True, payer_name="Sam")
        self.assertEqual(ctx["payment_state"], STATE_FAILED)
        self.assertTrue(ctx["show_payment_form"])

    def test_processing_when_checkout_started(self):
        link = SimpleNamespace(
            status=PAYMENT_STATUS_PENDING,
            amount_owed=Decimal("100"),
            payment_provider=None,
            paid_at=None,
            expense=SimpleNamespace(description="Lunch"),
            khalti_pidx="abc123",
            stripe_checkout_session_id=None,
        )
        ctx = build_payment_page_context(link=link, payer_name="Alex")
        self.assertEqual(ctx["payment_state"], STATE_PROCESSING)

    def test_pending_default(self):
        link = SimpleNamespace(
            status=PAYMENT_STATUS_PENDING,
            amount_owed=Decimal("25"),
            payment_provider=None,
            paid_at=None,
            expense=SimpleNamespace(description="Snacks"),
            khalti_pidx=None,
            stripe_checkout_session_id=None,
        )
        ctx = build_payment_page_context(link=link, payer_name="Ram")
        self.assertEqual(ctx["payment_state"], STATE_PENDING)
        self.assertTrue(ctx["show_payment_form"])


if __name__ == "__main__":
    unittest.main()
