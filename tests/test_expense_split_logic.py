"""Tests for itemized and equal expense split calculations."""

import unittest
from decimal import Decimal

from expense_split_logic import (
    ParsedLineItem,
    compute_equal_split,
    compute_itemized_split,
    to_decimal,
)
from money import split_sum_matches_total


class ExpenseSplitLogicTests(unittest.TestCase):
    def test_equal_split_simple(self):
        owed = compute_equal_split("90.0", [1, 2, 3])
        self.assertTrue(split_sum_matches_total(owed, Decimal("90")))
        self.assertEqual(len(owed), 3)

    def test_itemized_with_tax_tip(self):
        items = [
            ParsedLineItem(
                name="Pasta",
                price=to_decimal("20.0"),
                quantity=to_decimal("1.0"),
                assigned_user_ids=[1, 2],
            ),
            ParsedLineItem(
                name="Steak",
                price=to_decimal("40.0"),
                quantity=to_decimal("1.0"),
                assigned_user_ids=[2],
            ),
        ]
        owed, subtotal = compute_itemized_split(items, tax_tip_amount="10.0", member_ids=[1, 2, 3])

        self.assertEqual(subtotal, Decimal("60.00"))
        self.assertTrue(split_sum_matches_total(owed, Decimal("70.00")))
        self.assertAlmostEqual(float(owed[1]), 11.67, places=2)
        self.assertAlmostEqual(float(owed[2]), 58.33, places=2)
        self.assertEqual(owed.get(3, Decimal("0")), Decimal("0.00"))

    def test_itemized_requires_assignee(self):
        items = [
            ParsedLineItem(
                name="Salad",
                price=to_decimal("12.0"),
                quantity=to_decimal("1.0"),
                assigned_user_ids=[],
            ),
        ]
        with self.assertRaises(ValueError):
            compute_itemized_split(items, "0.0", [1, 2])


if __name__ == "__main__":
    unittest.main()
