"""Tests for in-app notifications."""

import os
import unittest
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy.pool import StaticPool

from app import app, db
from models import (
    NOTIFICATION_EXPENSE_ADDED,
    NOTIFICATION_PAYMENT_RECEIVED,
    NOTIFICATION_SETTLEMENT_COMPLETED,
    NOTIFICATION_SETTLEMENT_REQUESTED,
    Expense,
    ExpensePaymentLink,
    ExpenseSplit,
    Notification,
    PAYMENT_STATUS_PENDING,
    Trip,
    TripMember,
    User,
)
from notifications import (
    create_notification,
    get_notification_for_user,
    mark_all_read,
    mark_read,
    notify_expense_added,
    notify_payment_received,
    notify_settlement_links_created,
    notify_user_added_to_group,
    recent_notifications,
    unread_count,
)


class NotificationTests(unittest.TestCase):
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
            RECURRING_JOB_ENABLED=False,
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
        db.session.query(Notification).delete(synchronize_session=False)
        db.session.query(ExpensePaymentLink).delete(synchronize_session=False)
        db.session.query(ExpenseSplit).delete(synchronize_session=False)
        db.session.query(Expense).delete(synchronize_session=False)
        db.session.query(TripMember).delete(synchronize_session=False)
        db.session.query(Trip).delete(synchronize_session=False)
        db.session.query(User).delete(synchronize_session=False)
        db.session.commit()

        self.alice = User(name="Alice", email="alice@test.com", password="hash")
        self.bob = User(name="Bob", email="bob@test.com", password="hash")
        db.session.add_all([self.alice, self.bob])
        db.session.flush()

    def test_dedupe_key_prevents_duplicate_notifications(self):
        first = create_notification(
            self.alice.id,
            "Hello",
            kind="test",
            dedupe_key="event:1",
        )
        second = create_notification(
            self.alice.id,
            "Hello again",
            kind="test",
            dedupe_key="event:1",
        )
        db.session.commit()
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(Notification.query.filter_by(user_id=self.alice.id).count(), 1)

    def test_mark_read_only_affects_own_notifications(self):
        note = create_notification(self.alice.id, "Mine", kind="test")
        db.session.commit()
        self.assertFalse(mark_read(note.id, self.bob.id))
        self.assertTrue(mark_read(note.id, self.alice.id))
        db.session.commit()
        self.assertIsNotNone(note.read_at)

    def test_get_notification_for_user_scoped(self):
        note = create_notification(self.alice.id, "Private", kind="test")
        db.session.commit()
        self.assertIsNone(get_notification_for_user(note.id, self.bob.id))
        self.assertIsNotNone(get_notification_for_user(note.id, self.alice.id))

    def test_mark_all_read_and_unread_count(self):
        create_notification(self.alice.id, "One", kind="test")
        create_notification(self.alice.id, "Two", kind="test")
        db.session.commit()
        self.assertEqual(unread_count(self.alice.id), 2)
        updated = mark_all_read(self.alice.id)
        db.session.commit()
        self.assertEqual(updated, 2)
        self.assertEqual(unread_count(self.alice.id), 0)

    def test_recent_notifications_ordered_for_user(self):
        create_notification(self.alice.id, "Older", kind="test")
        create_notification(self.bob.id, "Other user", kind="test")
        create_notification(self.alice.id, "Newer", kind="test")
        db.session.commit()
        rows = recent_notifications(self.alice.id, limit=5)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].message, "Newer")

    def test_notify_expense_added_targets_other_members(self):
        trip = Trip(trip_name="Roommates", invite_code="ABC123", created_by=self.alice.id)
        db.session.add(trip)
        db.session.flush()
        db.session.add_all(
            [
                TripMember(trip_id=trip.id, user_id=self.alice.id),
                TripMember(trip_id=trip.id, user_id=self.bob.id),
            ]
        )
        expense = Expense(
            trip_id=trip.id,
            paid_by=self.alice.id,
            description="Groceries",
            amount=Decimal("40.00"),
        )
        db.session.add(expense)
        db.session.flush()
        db.session.add(
            ExpenseSplit(
                expense_id=expense.id,
                user_id=self.bob.id,
                amount_owed=Decimal("20.00"),
            )
        )
        notify_expense_added(expense, self.alice.id)
        db.session.commit()

        bob_notes = Notification.query.filter_by(user_id=self.bob.id).all()
        self.assertEqual(len(bob_notes), 1)
        self.assertEqual(bob_notes[0].kind, NOTIFICATION_EXPENSE_ADDED)
        self.assertEqual(Notification.query.filter_by(user_id=self.alice.id).count(), 0)

    def test_notify_payment_received_is_idempotent(self):
        expense = Expense(
            paid_by=self.alice.id,
            description="Dinner",
            amount=Decimal("100.00"),
        )
        db.session.add(expense)
        db.session.flush()
        link = ExpensePaymentLink(
            link_uuid="550e8400-e29b-41d4-a716-446655440000",
            expense_id=expense.id,
            user_id=self.bob.id,
            amount_owed=Decimal("50.00"),
            status=PAYMENT_STATUS_PENDING,
        )
        db.session.add(link)
        db.session.commit()

        notify_payment_received(link)
        notify_payment_received(link)
        db.session.commit()

        self.assertEqual(
            Notification.query.filter_by(
                user_id=self.alice.id,
                kind=NOTIFICATION_PAYMENT_RECEIVED,
            ).count(),
            1,
        )
        self.assertEqual(
            Notification.query.filter_by(
                user_id=self.bob.id,
                kind=NOTIFICATION_SETTLEMENT_COMPLETED,
            ).count(),
            1,
        )

    def test_notify_settlement_requested_skips_zero_amount_links(self):
        expense = Expense(
            paid_by=self.alice.id,
            description="Items",
            amount=Decimal("10.00"),
            self_service_items=True,
        )
        db.session.add(expense)
        db.session.flush()
        link = ExpensePaymentLink(
            link_uuid="660e8400-e29b-41d4-a716-446655440001",
            expense_id=expense.id,
            user_id=self.bob.id,
            amount_owed=Decimal("0.00"),
            status=PAYMENT_STATUS_PENDING,
        )
        db.session.add(link)
        db.session.commit()

        notify_settlement_links_created([link])
        db.session.commit()
        self.assertEqual(Notification.query.count(), 0)

    def test_notify_user_added_to_group_dedupes(self):
        notify_user_added_to_group(self.bob.id, 5, "Trip")
        notify_user_added_to_group(self.bob.id, 5, "Trip")
        db.session.commit()
        self.assertEqual(Notification.query.filter_by(user_id=self.bob.id).count(), 1)


if __name__ == "__main__":
    unittest.main()
