"""Tests for recurring expense date math."""

import unittest
from datetime import date

from recurring_expenses import (
    RECURRENCE_MONTHLY,
    RECURRENCE_WEEKLY,
    add_months,
    advance_recurrence,
    initial_next_occurrence,
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


if __name__ == "__main__":
    unittest.main()
