"""Tests for pairwise settle suggestion math."""

import unittest

from settle_suggestions import SETTLE_EPSILON, is_within_settle_suggestion


class SettleSuggestionThresholdTests(unittest.TestCase):
    def test_within_threshold(self):
        self.assertTrue(is_within_settle_suggestion(50.0, 200.0))
        self.assertTrue(is_within_settle_suggestion(-199.0, 200.0))

    def test_outside_threshold(self):
        self.assertFalse(is_within_settle_suggestion(0.0, 200.0))
        self.assertFalse(is_within_settle_suggestion(SETTLE_EPSILON / 2, 200.0))
        self.assertFalse(is_within_settle_suggestion(250.0, 200.0))


if __name__ == "__main__":
    unittest.main()
