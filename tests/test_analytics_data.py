"""Tests for analytics date filtering."""

import unittest
from datetime import datetime, timedelta

from analytics_data import filter_expenses_by_range, parse_range_key


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


if __name__ == "__main__":
    unittest.main()
