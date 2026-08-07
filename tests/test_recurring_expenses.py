"""Tests for recurring expense date math and generation job."""

import os
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy.pool import StaticPool

from app import app, db
from models import (
    RECURRENCE_MONTHLY,
    RECURRENCE_WEEKLY,
    Expense,
    ExpenseSplit,
    User,
)
from recurring_expenses import (
    add_months,
    advance_recurrence,
    find_due_recurring_templates,
    initial_next_occurrence,
    instance_exists_for_occurrence,
    is_template_active,
    process_recurring_template,
    run_recurring_expense_job,
)


class RecurringDateTests(unittest.TestCase):
    def test_weekly_advance(self):
        start = date(2026, 1, 15)
        self.assertEqual(advance_recurrence(start, RECURRENCE_WEEKLY), date(2026, 1, 22))

    def test_monthly_advance_clamps_end_of_month(self):
        start = date(2026, 1, 31)
        self.assertEqual(advance_recurrence(start, RECURRENCE_MONTHLY), date(2026, 2, 28))

    def test_initial_next_matches_advance(self):
        today = date(2026, 3, 10)
        self.assertEqual(
            initial_next_occurrence(today, RECURRENCE_MONTHLY),
            advance_recurrence(today, RECURRENCE_MONTHLY),
        )

    def test_add_months_leap_year(self):
        self.assertEqual(add_months(date(2024, 1, 31), 1), date(2024, 2, 29))

    def test_monthly_boundary_december_to_january(self):
        self.assertEqual(add_months(date(2026, 12, 15), 1), date(2027, 1, 15))


class RecurringProcessingTests(unittest.TestCase):
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
        db.session.query(ExpenseSplit).delete(synchronize_session=False)
        db.session.query(Expense).delete(synchronize_session=False)
        db.session.query(User).delete(synchronize_session=False)
        db.session.commit()

        self.payer = User(name="Payer", email="p@test.com", password="hash")
        db.session.add(self.payer)
        db.session.flush()

    def _template(
        self,
        *,
        next_date: date,
        interval: str = RECURRENCE_WEEKLY,
        end_date: date | None = None,
        is_recurring: bool = True,
        amount: str = "100.00",
    ) -> Expense:
        template = Expense(
            paid_by=self.payer.id,
            description="Rent",
            amount=Decimal(amount),
            is_recurring=is_recurring,
            recurrence_interval=interval,
            next_occurrence_date=next_date,
            recurrence_end_date=end_date,
        )
        db.session.add(template)
        db.session.flush()
        db.session.add(
            ExpenseSplit(
                expense_id=template.id,
                user_id=self.payer.id,
                amount_owed=Decimal(amount),
            )
        )
        db.session.commit()
        return template

    def test_generates_instance_and_advances_next(self):
        template = self._template(next_date=date(2026, 1, 10))
        created = process_recurring_template(template, date(2026, 1, 10))
        db.session.commit()

        self.assertEqual(created, 1)
        self.assertEqual(template.next_occurrence_date, date(2026, 1, 17))

        instances = Expense.query.filter_by(recurring_template_id=template.id).all()
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].recurrence_occurrence_date, date(2026, 1, 10))
        self.assertFalse(instances[0].is_recurring)

    def test_inactive_template_not_due(self):
        self._template(next_date=date(2026, 1, 10), is_recurring=False)
        due = find_due_recurring_templates(date(2026, 1, 15))
        self.assertEqual(due, [])

    def test_inactive_template_not_processed(self):
        template = self._template(next_date=date(2026, 1, 10), is_recurring=False)
        created = process_recurring_template(template, date(2026, 1, 15))
        self.assertEqual(created, 0)
        self.assertEqual(
            Expense.query.filter_by(recurring_template_id=template.id).count(),
            0,
        )

    def test_expired_template_stops_after_last_occurrence(self):
        template = self._template(
            next_date=date(2026, 1, 10),
            end_date=date(2026, 1, 17),
        )
        created = process_recurring_template(template, date(2026, 2, 1))
        db.session.commit()

        self.assertEqual(created, 2)
        self.assertFalse(template.is_recurring)
        self.assertIsNone(template.next_occurrence_date)
        self.assertEqual(
            Expense.query.filter_by(recurring_template_id=template.id).count(),
            2,
        )

    def test_expired_before_first_occurrence_generates_nothing(self):
        template = self._template(
            next_date=date(2026, 2, 1),
            end_date=date(2026, 1, 31),
        )
        created = process_recurring_template(template, date(2026, 2, 1))
        db.session.commit()

        self.assertEqual(created, 0)
        self.assertFalse(template.is_recurring)
        self.assertIsNone(template.next_occurrence_date)

    def test_duplicate_prevention_on_second_run(self):
        template = self._template(next_date=date(2026, 1, 10))
        first = process_recurring_template(template, date(2026, 1, 10))
        db.session.commit()
        self.assertEqual(first, 1)

        template.next_occurrence_date = date(2026, 1, 10)
        second = process_recurring_template(template, date(2026, 1, 10))
        db.session.commit()
        self.assertEqual(second, 0)
        self.assertEqual(
            Expense.query.filter_by(recurring_template_id=template.id).count(),
            1,
        )

    def test_instance_exists_for_occurrence(self):
        template = self._template(next_date=date(2026, 1, 10))
        self.assertFalse(instance_exists_for_occurrence(template.id, date(2026, 1, 10)))

        db.session.add(
            Expense(
                paid_by=self.payer.id,
                description="Copy",
                amount=Decimal("100.00"),
                recurring_template_id=template.id,
                recurrence_occurrence_date=date(2026, 1, 10),
            )
        )
        db.session.commit()
        self.assertTrue(instance_exists_for_occurrence(template.id, date(2026, 1, 10)))

    def test_catch_up_missed_occurrences(self):
        template = self._template(next_date=date(2026, 1, 1))
        created = process_recurring_template(template, date(2026, 1, 22))
        db.session.commit()

        self.assertEqual(created, 4)
        self.assertEqual(template.next_occurrence_date, date(2026, 1, 29))
        dates = sorted(
            row.recurrence_occurrence_date
            for row in Expense.query.filter_by(recurring_template_id=template.id).all()
        )
        self.assertEqual(
            dates,
            [date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 15), date(2026, 1, 22)],
        )

    def test_run_job_uses_explicit_as_of(self):
        self._template(next_date=date(2026, 3, 1))
        stats = run_recurring_expense_job(self.app, as_of=date(2026, 3, 1))
        self.assertEqual(stats["created"], 1)
        self.assertEqual(stats["errors"], 0)

    @patch("recurring_expenses.utc_today", return_value=date(2026, 4, 1))
    def test_run_job_defaults_to_utc_today(self, _mock_today):
        self._template(next_date=date(2026, 4, 1))
        stats = run_recurring_expense_job(self.app)
        self.assertEqual(stats["created"], 1)

    def test_is_template_active_respects_end_date(self):
        template = self._template(
            next_date=date(2026, 2, 1),
            end_date=date(2026, 1, 31),
        )
        self.assertFalse(is_template_active(template, date(2026, 2, 1)))

    def test_failed_generation_retries_next_run(self):
        from payment_links import create_expense_payment_links as real_create_links

        self._template(next_date=date(2026, 1, 10))
        with patch(
            "recurring_expenses.create_expense_payment_links",
            side_effect=RuntimeError("boom"),
        ):
            stats = run_recurring_expense_job(self.app, as_of=date(2026, 1, 10))
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(stats["created"], 0)

        template = Expense.query.filter_by(is_recurring=True).one()
        self.assertEqual(template.next_occurrence_date, date(2026, 1, 10))

        with patch(
            "recurring_expenses.create_expense_payment_links",
            wraps=real_create_links,
        ):
            stats = run_recurring_expense_job(self.app, as_of=date(2026, 1, 10))
        db.session.refresh(template)
        self.assertEqual(stats["created"], 1)
        self.assertEqual(template.next_occurrence_date, date(2026, 1, 17))


if __name__ == "__main__":
    unittest.main()
