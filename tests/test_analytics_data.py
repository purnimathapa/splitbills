"""Tests for analytics SQL aggregations."""

import os
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy.pool import StaticPool

from app import app, db
from analytics_data import filter_expenses_by_range, parse_range_key
from models import (
    PAYMENT_STATUS_PAID,
    PAYMENT_STATUS_PENDING,
    RECURRENCE_MONTHLY,
    Expense,
    ExpensePaymentLink,
    ExpenseSplit,
    Trip,
    TripMember,
    User,
)
from services.analytics import (
    aggregate_category_spending,
    aggregate_recurring_metrics,
    aggregate_settlement_metrics,
    aggregate_total_spending,
    build_user_analytics,
    visible_expense_filter,
)


class _Expense:
    def __init__(self, amount, created_at=None):
        self.amount = amount
        self.created_at = created_at


class AnalyticsFilterTests(unittest.TestCase):
    def test_parse_range_defaults(self):
        self.assertEqual(parse_range_key(None), "30")
        self.assertEqual(parse_range_key("all"), "all")

    def test_filter_30_days(self):
        now = datetime.utcnow()
        old = _Expense(10, now - timedelta(days=40))
        recent = _Expense(20, now - timedelta(days=5))
        out = filter_expenses_by_range([old, recent], "30")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].amount, 20)


class AnalyticsAggregationTests(unittest.TestCase):
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
        for model in (ExpensePaymentLink, ExpenseSplit, Expense, TripMember, Trip, User):
            db.session.query(model).delete(synchronize_session=False)
        db.session.commit()

        self.alice = User(name="Alice", email="alice@test.com", password="hash")
        self.bob = User(name="Bob", email="bob@test.com", password="hash")
        db.session.add_all([self.alice, self.bob])
        db.session.flush()

        self.active_trip = Trip(
            trip_name="Roommates",
            invite_code="ACT001",
            created_by=self.alice.id,
            is_active=True,
        )
        self.archived_trip = Trip(
            trip_name="Old Trip",
            invite_code="ARC001",
            created_by=self.alice.id,
            is_active=False,
        )
        db.session.add_all([self.active_trip, self.archived_trip])
        db.session.flush()
        db.session.add_all(
            [
                TripMember(trip_id=self.active_trip.id, user_id=self.alice.id),
                TripMember(trip_id=self.active_trip.id, user_id=self.bob.id),
                TripMember(trip_id=self.archived_trip.id, user_id=self.alice.id),
            ]
        )
        db.session.commit()

    def _add_expense(
        self,
        *,
        trip_id: int | None,
        paid_by: int,
        amount: str,
        category: str | None = "Food",
        created_at: datetime | None = None,
        is_recurring: bool = False,
    ) -> Expense:
        expense = Expense(
            trip_id=trip_id,
            paid_by=paid_by,
            description="Test",
            amount=Decimal(amount),
            category=category,
            is_recurring=is_recurring,
            created_at=created_at or datetime.utcnow(),
        )
        db.session.add(expense)
        db.session.flush()
        if trip_id:
            db.session.add(
                ExpenseSplit(
                    expense_id=expense.id,
                    user_id=self.bob.id if paid_by == self.alice.id else self.alice.id,
                    amount_owed=Decimal(amount) / 2,
                )
            )
        db.session.commit()
        return expense

    def test_empty_dataset_returns_zeros(self):
        stats = build_user_analytics(self.alice.id, "30")
        self.assertEqual(stats.total_spending, Decimal("0"))
        self.assertEqual(stats.expense_count, 0)
        self.assertEqual(stats.category_labels, [])
        self.assertEqual(stats.settlement.amount_owed, Decimal("0"))

    def test_total_spending_excludes_recurring_templates(self):
        self._add_expense(
            trip_id=self.active_trip.id,
            paid_by=self.alice.id,
            amount="100.00",
        )
        self._add_expense(
            trip_id=self.active_trip.id,
            paid_by=self.alice.id,
            amount="50.00",
            is_recurring=True,
        )
        total, count = aggregate_total_spending(self.alice.id, "all")
        self.assertEqual(total, Decimal("100.00"))
        self.assertEqual(count, 1)

    def test_archived_group_spending_excluded(self):
        self._add_expense(
            trip_id=self.active_trip.id,
            paid_by=self.alice.id,
            amount="40.00",
        )
        self._add_expense(
            trip_id=self.archived_trip.id,
            paid_by=self.alice.id,
            amount="999.00",
        )
        total, _count = aggregate_total_spending(self.alice.id, "all")
        self.assertEqual(total, Decimal("40.00"))

    def test_missing_category_defaults_to_general(self):
        self._add_expense(
            trip_id=self.active_trip.id,
            paid_by=self.alice.id,
            amount="25.00",
            category=None,
        )
        self._add_expense(
            trip_id=self.active_trip.id,
            paid_by=self.alice.id,
            amount="10.00",
            category="  ",
        )
        labels, values = aggregate_category_spending(self.alice.id, "all")
        self.assertEqual(labels, ["General"])
        self.assertEqual(values, [35.0])

    def test_group_vs_personal_spending(self):
        self._add_expense(
            trip_id=self.active_trip.id,
            paid_by=self.alice.id,
            amount="80.00",
        )
        self._add_expense(
            trip_id=None,
            paid_by=self.alice.id,
            amount="20.00",
        )
        stats = build_user_analytics(self.alice.id, "all")
        self.assertEqual(stats.group_spending, Decimal("80.00"))
        self.assertEqual(stats.personal_spending, Decimal("20.00"))
        self.assertEqual(stats.total_spending, Decimal("100.00"))

    def test_settlement_metrics_from_payment_links(self):
        expense = self._add_expense(
            trip_id=self.active_trip.id,
            paid_by=self.alice.id,
            amount="100.00",
        )
        paid_expense = self._add_expense(
            trip_id=self.active_trip.id,
            paid_by=self.alice.id,
            amount="40.00",
        )
        pending = ExpensePaymentLink(
            link_uuid="11111111-1111-1111-1111-111111111111",
            expense_id=expense.id,
            user_id=self.bob.id,
            amount_owed=Decimal("50.00"),
            status=PAYMENT_STATUS_PENDING,
        )
        paid = ExpensePaymentLink(
            link_uuid="22222222-2222-2222-2222-222222222222",
            expense_id=paid_expense.id,
            user_id=self.bob.id,
            amount_owed=Decimal("25.00"),
            status=PAYMENT_STATUS_PAID,
        )
        db.session.add_all([pending, paid])
        db.session.commit()

        settlement = aggregate_settlement_metrics(self.alice.id)
        self.assertEqual(settlement.amount_receivable, Decimal("50.00"))
        self.assertEqual(settlement.pending_count, 1)
        self.assertEqual(settlement.paid_count, 1)

        bob_settlement = aggregate_settlement_metrics(self.bob.id)
        self.assertEqual(bob_settlement.amount_owed, Decimal("50.00"))

    def test_recurring_metrics(self):
        template = Expense(
            trip_id=self.active_trip.id,
            paid_by=self.alice.id,
            description="Rent",
            amount=Decimal("1200.00"),
            is_recurring=True,
            recurrence_interval=RECURRENCE_MONTHLY,
            next_occurrence_date=date(2026, 4, 1),
        )
        db.session.add(template)
        db.session.commit()

        recurring = aggregate_recurring_metrics(self.alice.id)
        self.assertEqual(recurring.active_count, 1)
        self.assertEqual(recurring.monthly_count, 1)
        self.assertEqual(recurring.per_occurrence_total, Decimal("1200.00"))

    def test_date_range_filters_old_expenses(self):
        self._add_expense(
            trip_id=self.active_trip.id,
            paid_by=self.alice.id,
            amount="10.00",
            created_at=datetime.utcnow() - timedelta(days=5),
        )
        self._add_expense(
            trip_id=self.active_trip.id,
            paid_by=self.alice.id,
            amount="90.00",
            created_at=datetime.utcnow() - timedelta(days=60),
        )
        total, count = aggregate_total_spending(self.alice.id, "30")
        self.assertEqual(total, Decimal("10.00"))
        self.assertEqual(count, 1)

    def test_visible_expense_filter_is_scoped_to_user(self):
        other = User(name="Carol", email="carol@test.com", password="hash")
        db.session.add(other)
        db.session.flush()
        private = Expense(
            trip_id=None,
            paid_by=other.id,
            description="Private",
            amount=Decimal("500.00"),
        )
        db.session.add(private)
        db.session.commit()

        visible_ids = [
            row.id
            for row in Expense.query.filter(
                visible_expense_filter(self.alice.id, active_trips_only=True)
            ).all()
        ]
        self.assertNotIn(private.id, visible_ids)


if __name__ == "__main__":
    unittest.main()
