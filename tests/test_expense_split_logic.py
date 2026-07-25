"""Tests for itemized and equal expense split calculations."""

import unittest

from expense_split_logic import (
    ParsedLineItem,
    compute_equal_split,
    compute_itemized_split,
)


class ExpenseSplitLogicTests(unittest.TestCase):
    def test_equal_split_simple(self):
        owed = compute_equal_split(90.0, [1, 2, 3])
        self.assertAlmostEqual(sum(owed.values()), 90.0, places=2)
        self.assertEqual(len(owed), 3)

    def test_itemized_with_tax_tip(self):
        items = [
            ParsedLineItem(
                name="Pasta",
                price=20.0,
                quantity=1.0,
                assigned_user_ids=[1, 2],
            ),
            ParsedLineItem(
                name="Steak",
                price=40.0,
                quantity=1.0,
                assigned_user_ids=[2],
            ),
        ]
        owed, subtotal = compute_itemized_split(items, tax_tip_amount=10.0, member_ids=[1, 2, 3])

        self.assertEqual(subtotal, 60.0)
        self.assertAlmostEqual(sum(owed.values()), 70.0, places=2)
        # User 1: 10 pasta + tax share on 10/60 of 10
        self.assertAlmostEqual(owed[1], 11.67, places=2)
        # User 2: 10 pasta + 40 steak + tax on 50/60 of 10
        self.assertAlmostEqual(owed[2], 58.33, places=2)
        self.assertAlmostEqual(owed.get(3, 0.0), 0.0, places=2)

    def test_itemized_requires_assignee(self):
        items = [
            ParsedLineItem(
                name="Salad",
                price=12.0,
                quantity=1.0,
                assigned_user_ids=[],
            ),
        ]
        with self.assertRaises(ValueError):
            compute_itemized_split(items, 0.0, [1, 2])


if __name__ == "__main__":
    unittest.main()
