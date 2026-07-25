"""Pure math tests for pairwise balance on a single expense."""

import unittest

from settle_suggestions import pairwise_net_on_expense


class PairwiseNetOnExpenseTests(unittest.TestCase):
    def test_payer_owed_by_other_equal_split(self):
        net = pairwise_net_on_expense(
            amount=100.0,
            paid_by=1,
            viewer_user_id=1,
            other_user_id=2,
            split_map={},
            member_count=2,
        )
        self.assertEqual(net, 50.0)

    def test_viewer_owes_payer_equal_split(self):
        net = pairwise_net_on_expense(
            amount=90.0,
            paid_by=2,
            viewer_user_id=1,
            other_user_id=2,
            split_map={},
            member_count=3,
        )
        self.assertAlmostEqual(net, -30.0)

    def test_explicit_splits(self):
        net = pairwise_net_on_expense(
            amount=100.0,
            paid_by=1,
            viewer_user_id=1,
            other_user_id=2,
            split_map={1: 40.0, 2: 60.0},
            member_count=2,
        )
        self.assertEqual(net, 60.0)


if __name__ == "__main__":
    unittest.main()
