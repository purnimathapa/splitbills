"""Tests for self-service item claim split math."""

import unittest

from expense_split_logic import ParsedLineItem, compute_itemized_split
from money import quantize_money


class SelfServiceItemSplitTests(unittest.TestCase):
    def test_shared_item_split_between_two(self):
        items = [
            ParsedLineItem("Nachos", 20.0, 1.0, [1, 2]),
        ]
        owed, sub = compute_itemized_split(items, 0.0, [1, 2, 3])
        self.assertEqual(sub, 20.0)
        self.assertEqual(owed[1], 10.0)
        self.assertEqual(owed[2], 10.0)
        self.assertEqual(owed.get(3, 0), 0.0)

    def test_item_claimed_by_everyone(self):
        items = [
            ParsedLineItem("Bread", 12.0, 1.0, [1, 2, 3]),
        ]
        owed, _ = compute_itemized_split(items, 0.0, [1, 2, 3])
        self.assertEqual(owed[1], 4.0)
        self.assertEqual(owed[2], 4.0)
        self.assertEqual(owed[3], 4.0)

    def test_tax_tip_proportional_to_claimed_subtotals(self):
        items = [
            ParsedLineItem("Burger", 10.0, 1.0, [1]),
            ParsedLineItem("Salad", 10.0, 1.0, [2]),
        ]
        owed, sub = compute_itemized_split(items, 4.0, [1, 2])
        self.assertEqual(sub, 20.0)
        self.assertEqual(sum(owed.values()), 24.0)
        self.assertEqual(owed[1], 12.0)
        self.assertEqual(owed[2], 12.0)

    def test_zero_claimers_excluded_from_parsed_list(self):
        """Caller must filter items with no assignees before compute_itemized_split."""
        items = [
            ParsedLineItem("Orphan", 5.0, 1.0, []),
            ParsedLineItem("Tea", 3.0, 1.0, [1]),
        ]
        active = [i for i in items if i.assigned_user_ids]
        owed, sub = compute_itemized_split(active, 0.0, [1])
        self.assertEqual(sub, 3.0)
        self.assertEqual(owed[1], 3.0)

    def test_single_claimer_pays_full_item(self):
        items = [ParsedLineItem("Steak", 30.0, 1.0, [2])]
        owed, _ = compute_itemized_split(items, 0.0, [1, 2])
        self.assertEqual(owed[2], 30.0)
        self.assertEqual(owed.get(1, 0), 0.0)


if __name__ == "__main__":
    unittest.main()
