"""Tests for PaymentService with mocked Khalti/Stripe providers."""

import os
import unittest
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy.pool import StaticPool

from app import app, db
from models import (
    PAYMENT_STATUS_PAID,
    PAYMENT_STATUS_PENDING,
    Expense,
    ExpensePaymentLink,
    User,
)
from services import payment_service


class PaymentServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_ENGINE_OPTIONS={
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            },
            SECRET_KEY="test-secret",
            KHALTI_SECRET_KEY="test_khalti_key",
            STRIPE_SECRET_KEY="sk_test_fake",
            STRIPE_WEBHOOK_SECRET="whsec_test",
            KHALTI_WEBHOOK_SECRET="khalti_whsec",
        )
        cls.ctx = cls.app.app_context()
        cls.ctx.push()
        db.engine.dispose()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.ctx.pop()

    def setUp(self):
        db.session.query(ExpensePaymentLink).delete(synchronize_session=False)
        db.session.query(Expense).delete(synchronize_session=False)
        db.session.query(User).delete(synchronize_session=False)
        db.session.commit()

        payer = User(name="Payer", email="p@test.com", password="hash")
        guest = User(name="Guest", email="g@test.com", password="hash")
        db.session.add_all([payer, guest])
        db.session.flush()
        expense = Expense(
            paid_by=payer.id,
            description="Dinner",
            amount=Decimal("100.00"),
        )
        db.session.add(expense)
        db.session.flush()
        self.link = ExpensePaymentLink(
            link_uuid="550e8400-e29b-41d4-a716-446655440000",
            expense_id=expense.id,
            user_id=guest.id,
            amount_owed=Decimal("50.00"),
            status=PAYMENT_STATUS_PENDING,
        )
        db.session.add(self.link)
        db.session.commit()

    def test_mark_paid_is_idempotent(self):
        with self.app.test_request_context():
            first = payment_service.mark_payment_link_paid(self.link, "manual")
            self.assertTrue(first.settled)
            self.assertFalse(first.already_paid)

            second = payment_service.mark_payment_link_paid(self.link, "manual")
            self.assertTrue(second.settled)
            self.assertTrue(second.already_paid)

        db.session.refresh(self.link)
        self.assertEqual(self.link.status, PAYMENT_STATUS_PAID)

    @patch("services.payments.khalti.lookup_payment")
    def test_settle_khalti_success(self, mock_lookup):
        mock_lookup.return_value = {
            "status": "Completed",
            "total_amount": 5000,
            "purchase_order_id": f"splitbills-link-{self.link.id}",
        }
        with self.app.test_request_context():
            result = payment_service.settle_khalti(self.link, "pidx-123")
        self.assertTrue(result.settled)
        db.session.refresh(self.link)
        self.assertEqual(self.link.status, PAYMENT_STATUS_PAID)
        self.assertEqual(self.link.payment_provider, "khalti")
        self.assertEqual(self.link.khalti_pidx, "pidx-123")

    @patch("services.payments.khalti.lookup_payment")
    def test_settle_khalti_pending_payment(self, mock_lookup):
        mock_lookup.return_value = {
            "status": "Pending",
            "total_amount": 5000,
            "purchase_order_id": f"splitbills-link-{self.link.id}",
        }
        with self.app.test_request_context():
            result = payment_service.settle_khalti(self.link, "pidx-pending")
        self.assertFalse(result.settled)
        self.assertIn("not_completed", result.detail)
        db.session.refresh(self.link)
        self.assertEqual(self.link.status, PAYMENT_STATUS_PENDING)

    @patch("services.payments.khalti.lookup_payment")
    def test_settle_khalti_duplicate_is_idempotent(self, mock_lookup):
        mock_lookup.return_value = {
            "status": "Completed",
            "total_amount": 5000,
            "purchase_order_id": f"splitbills-link-{self.link.id}",
        }
        with self.app.test_request_context():
            payment_service.settle_khalti(self.link, "pidx-dup")
            result = payment_service.settle_khalti(self.link, "pidx-dup")
        self.assertTrue(result.already_paid)

    @patch("services.payments.stripe.retrieve_checkout_session")
    def test_settle_stripe_success(self, mock_retrieve):
        mock_retrieve.return_value = {
            "id": "cs_test_1",
            "payment_status": "paid",
            "client_reference_id": str(self.link.id),
            "metadata": {"payment_link_id": str(self.link.id)},
            "amount_total": 5000,
        }
        with self.app.test_request_context():
            result = payment_service.settle_stripe(self.link, "cs_test_1")
        self.assertTrue(result.settled)
        db.session.refresh(self.link)
        self.assertEqual(self.link.payment_provider, "stripe")

    @patch("services.payments.khalti.initiate_payment")
    def test_start_khalti_checkout(self, mock_initiate):
        mock_initiate.return_value = {
            "payment_url": "https://pay.khalti.com/x",
            "pidx": "new-pidx",
        }
        with self.app.test_request_context():
            checkout = payment_service.start_khalti_checkout(
                self.link,
                return_url="https://app/pay",
                website_url="https://app",
                description="Test",
                customer_name="Guest",
            )
        self.assertEqual(checkout.redirect_url, "https://pay.khalti.com/x")
        db.session.refresh(self.link)
        self.assertEqual(self.link.khalti_pidx, "new-pidx")

    @patch("services.payments.stripe.construct_webhook_event")
    @patch("services.payments.stripe.retrieve_checkout_session")
    def test_process_stripe_webhook(self, mock_retrieve, mock_construct):
        mock_construct.return_value = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_wh_1",
                    "metadata": {"payment_link_id": str(self.link.id)},
                    "client_reference_id": str(self.link.id),
                }
            },
        }
        mock_retrieve.return_value = {
            "id": "cs_wh_1",
            "payment_status": "paid",
            "client_reference_id": str(self.link.id),
            "metadata": {"payment_link_id": str(self.link.id)},
            "amount_total": 5000,
        }
        with self.app.test_request_context():
            body, status = payment_service.process_stripe_webhook(b"{}", "sig")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("received"))
        db.session.refresh(self.link)
        self.assertEqual(self.link.status, PAYMENT_STATUS_PAID)

    @patch("services.payments.khalti.lookup_payment")
    def test_process_khalti_webhook_rejects_bad_secret(self, mock_lookup):
        with self.app.test_request_context():
            body, status = payment_service.process_khalti_webhook(
                {"pidx": "pidx-1"},
                webhook_secret_header="wrong",
            )
        self.assertEqual(status, 401)
        mock_lookup.assert_not_called()

    @patch("services.payments.khalti.lookup_payment")
    def test_process_khalti_webhook_success(self, mock_lookup):
        self.link.khalti_pidx = "pidx-wh"
        db.session.commit()
        mock_lookup.return_value = {
            "status": "Completed",
            "total_amount": 5000,
            "purchase_order_id": f"splitbills-link-{self.link.id}",
        }
        with self.app.test_request_context():
            body, status = payment_service.process_khalti_webhook(
                {"pidx": "pidx-wh"},
                webhook_secret_header="khalti_whsec",
            )
        self.assertEqual(status, 200)
        self.assertEqual(body.get("status"), "paid")


if __name__ == "__main__":
    unittest.main()
