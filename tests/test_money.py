"""Tests for Decimal money math and split-total invariants."""

import unittest
from decimal import Decimal

from expense_split_logic import (
    compute_equal_split,
    compute_exact_split,
    compute_percentage_split,
    compute_shares_split,
)
from money import (
    assert_split_covers_total,
    fix_rounding_drift,
    quantize_money,
    split_sum_matches_total,
    to_smallest_currency_unit,
)


class EqualSplitTests(unittest.TestCase):
    def test_100_div_3_people_sums_to_total(self):
        owed = compute_equal_split("100", [1, 2, 3])
        self.assertEqual(len(owed), 3)
        self.assertTrue(split_sum_matches_total(owed, Decimal("100")))
        self.assertEqual(sum(owed.values()), Decimal("100.00"))

    def test_10_split_between_3_people(self):
        owed = compute_equal_split("10.00", [1, 2, 3])
        self.assertTrue(split_sum_matches_total(owed, Decimal("10.00")))
        # 10 / 3 → 3.33 + 3.33 + 3.34 after drift fix
        self.assertEqual(sum(owed.values()), Decimal("10.00"))

    def test_two_person_penny_split(self):
        owed = compute_equal_split("0.01", [1, 2])
        self.assertTrue(split_sum_matches_total(owed, Decimal("0.01")))


class PercentageSplitTests(unittest.TestCase):
    def test_percentage_split_sums_to_total(self):
        owed = compute_percentage_split("100", {1: "33.33", 2: "33.33", 3: "33.34"})
        self.assertTrue(split_sum_matches_total(owed, Decimal("100")))
        self.assertEqual(sum(owed.values()), Decimal("100.00"))

    def test_rejects_percentages_over_100(self):
        with self.assertRaises(ValueError):
            compute_percentage_split("100", {1: "60", 2: "50"})


class ExactSplitTests(unittest.TestCase):
    def test_exact_split_sums_to_total(self):
        owed = compute_exact_split("10.00", {1: "3.33", 2: "3.33", 3: "3.34"})
        self.assertTrue(split_sum_matches_total(owed, Decimal("10.00")))

    def test_rejects_mismatch(self):
        with self.assertRaises(ValueError):
            compute_exact_split("10.00", {1: "3.00", 2: "3.00"})


class SharesSplitTests(unittest.TestCase):
    def test_shares_split_sums_to_total(self):
        owed = compute_shares_split("100", {1: "1", 2: "1", 3: "1"})
        self.assertTrue(split_sum_matches_total(owed, Decimal("100")))


class RoundingRemainderTests(unittest.TestCase):
    def test_fix_rounding_drift_assigns_leftover_cent(self):
        shares = {1: Decimal("33.33"), 2: Decimal("33.33"), 3: Decimal("33.33")}
        fix_rounding_drift(shares, Decimal("100.00"))
        self.assertEqual(sum(shares.values()), Decimal("100.00"))

    def test_small_decimal_values(self):
        owed = compute_equal_split("0.03", [1, 2, 3])
        self.assertTrue(split_sum_matches_total(owed, Decimal("0.03")))


class PaymentGatewayConversionTests(unittest.TestCase):
    def test_to_smallest_currency_unit_no_float_drift(self):
        self.assertEqual(to_smallest_currency_unit("10.00"), 1000)
        self.assertEqual(to_smallest_currency_unit("9.99"), 999)


class AssertSplitCoversTotalTests(unittest.TestCase):
    def test_passes_when_sum_matches(self):
        assert_split_covers_total(Decimal("100"), {1: Decimal("60"), 2: Decimal("40")})

    def test_raises_when_sum_does_not_match(self):
        with self.assertRaises(ValueError):
            assert_split_covers_total(Decimal("100"), {1: Decimal("50"), 2: Decimal("40")})


if __name__ == "__main__":
    unittest.main()
