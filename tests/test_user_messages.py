"""Tests for user-facing error copy."""

import unittest

from services.user_messages import GENERIC_ERROR, user_facing_error


class UserMessagesTests(unittest.TestCase):
    def test_value_error_uses_message(self):
        self.assertEqual(user_facing_error(ValueError("Enter a valid amount.")), "Enter a valid amount.")

    def test_empty_value_error_is_generic(self):
        self.assertEqual(user_facing_error(ValueError("")), GENERIC_ERROR)

    def test_other_exceptions_are_generic(self):
        self.assertEqual(user_facing_error(RuntimeError("db connection failed")), GENERIC_ERROR)
        self.assertNotIn("db", user_facing_error(RuntimeError("db connection failed")))
