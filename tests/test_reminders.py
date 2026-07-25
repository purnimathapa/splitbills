"""Tests for payment reminder eligibility."""

import unittest
from datetime import datetime, timedelta

from reminders import is_link_due_for_reminder


class ReminderDueTests(unittest.TestCase):
    def test_not_due_if_balance_too_new(self):
        now = datetime(2026, 1, 10, 12, 0, 0)
        created = now - timedelta(days=1)
        self.assertFalse(
            is_link_due_for_reminder(created, None, 3, now=now)
        )

    def test_due_if_old_and_never_reminded(self):
        now = datetime(2026, 1, 10, 12, 0, 0)
        created = now - timedelta(days=10)
        self.assertTrue(
            is_link_due_for_reminder(created, None, 3, now=now)
        )

    def test_not_due_if_reminded_recently(self):
        now = datetime(2026, 1, 10, 12, 0, 0)
        created = now - timedelta(days=10)
        last_sent = now - timedelta(days=1)
        self.assertFalse(
            is_link_due_for_reminder(created, last_sent, 3, now=now)
        )

    def test_due_if_reminded_outside_interval(self):
        now = datetime(2026, 1, 10, 12, 0, 0)
        created = now - timedelta(days=20)
        last_sent = now - timedelta(days=5)
        self.assertTrue(
            is_link_due_for_reminder(created, last_sent, 3, now=now)
        )


if __name__ == "__main__":
    unittest.main()
