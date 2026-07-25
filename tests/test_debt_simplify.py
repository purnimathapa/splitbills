"""Unit tests for debt_simplify.simplify_debts."""

import unittest

from debt_simplify import SETTLEMENT_EPSILON, simplify_debts


def _apply_transactions(
    balances: dict[int, float],
    transactions: list[dict],
) -> dict[int, float]:
    """Simulate payments to verify settlements clear balances."""
    result = dict(balances)
    for txn in transactions:
        result[txn["from_user"]] = result.get(txn["from_user"], 0) + txn["amount"]
        result[txn["to_user"]] = result.get(txn["to_user"], 0) - txn["amount"]
    return result


class SimplifyDebtsTests(unittest.TestCase):
    def test_two_person_simple(self):
        balances = {1: -75.0, 2: 75.0}
        txns = simplify_debts(balances)

        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0]["from_user"], 1)
        self.assertEqual(txns[0]["to_user"], 2)
        self.assertEqual(txns[0]["amount"], 75.0)

    def test_three_or_more_persons(self):
        # User 3 fronted the bill; users 1 and 2 owe them.
        balances = {1: -30.0, 2: -20.0, 3: 50.0}
        txns = simplify_debts(balances)

        self.assertGreaterEqual(len(txns), 1)
        self.assertLessEqual(len(txns), 2)  # optimal: n - 1 for n = 3

        total_paid = sum(t["amount"] for t in txns)
        self.assertAlmostEqual(total_paid, 50.0, places=2)

        self.assertEqual({t["to_user"] for t in txns}, {3})
        self.assertEqual({t["from_user"] for t in txns}, {1, 2})

        after = _apply_transactions(balances, txns)
        for user_id, balance in after.items():
            self.assertLess(abs(balance), SETTLEMENT_EPSILON)

    def test_four_person_chain_style_balances(self):
        balances = {10: 40.0, 20: -15.0, 30: -25.0, 40: 0.0}
        txns = simplify_debts(balances)

        non_zero_count = sum(
            1 for b in balances.values() if abs(b) >= SETTLEMENT_EPSILON
        )
        self.assertLessEqual(len(txns), non_zero_count - 1)

        after = _apply_transactions(balances, txns)
        for user_id, balance in after.items():
            self.assertLess(abs(balance), SETTLEMENT_EPSILON)

        self.assertEqual({t["to_user"] for t in txns}, {10})

    def test_already_settled_empty_result(self):
        self.assertEqual(simplify_debts({}), [])
        self.assertEqual(simplify_debts({1: 0.0, 2: 0.0, 3: 0.0}), [])
        self.assertEqual(simplify_debts({5: 0.004, 6: -0.004}), [])

    def test_raises_when_balances_do_not_sum_to_zero(self):
        with self.assertRaises(ValueError):
            simplify_debts({1: -50.0, 2: 40.0})


if __name__ == "__main__":
    unittest.main()
