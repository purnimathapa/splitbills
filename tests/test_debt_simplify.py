"""Unit tests for debt_simplify."""

import unittest
from decimal import Decimal

from debt_simplify import (
    SETTLEMENT_EPSILON,
    apply_settlements,
    greedy_settle,
    simplify_debts,
    verify_settlements,
)


class SimplifyDebtsTests(unittest.TestCase):
    def test_two_person_simple(self):
        balances = {1: Decimal("-75"), 2: Decimal("75")}
        txns = simplify_debts(balances)

        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0]["from_user"], 1)
        self.assertEqual(txns[0]["to_user"], 2)
        self.assertEqual(txns[0]["amount"], Decimal("75.00"))
        self.assertTrue(
            verify_settlements(balances, txns, payer_key="from_user", payee_key="to_user")
        )

    def test_three_or_more_persons(self):
        balances = {1: Decimal("-30"), 2: Decimal("-20"), 3: Decimal("50")}
        txns = simplify_debts(balances)

        self.assertGreaterEqual(len(txns), 1)
        self.assertLessEqual(len(txns), 2)

        total_paid = sum(t["amount"] for t in txns)
        self.assertEqual(total_paid, Decimal("50.00"))
        self.assertEqual({t["to_user"] for t in txns}, {3})
        self.assertEqual({t["from_user"] for t in txns}, {1, 2})
        self.assertTrue(
            verify_settlements(balances, txns, payer_key="from_user", payee_key="to_user")
        )

    def test_four_person_chain_style_balances(self):
        balances = {10: Decimal("40"), 20: Decimal("-15"), 30: Decimal("-25"), 40: Decimal("0")}
        txns = simplify_debts(balances)

        non_zero_count = sum(
            1 for b in balances.values() if abs(b) >= SETTLEMENT_EPSILON
        )
        self.assertLessEqual(len(txns), non_zero_count - 1)
        self.assertTrue(
            verify_settlements(balances, txns, payer_key="from_user", payee_key="to_user")
        )
        self.assertEqual({t["to_user"] for t in txns}, {10})

    def test_already_settled_empty_result(self):
        self.assertEqual(simplify_debts({}), [])
        self.assertEqual(simplify_debts({1: Decimal("0"), 2: Decimal("0")}), [])
        self.assertEqual(simplify_debts({5: Decimal("0.004"), 6: Decimal("-0.004")}), [])

    def test_raises_when_balances_do_not_sum_to_zero(self):
        with self.assertRaises(ValueError):
            simplify_debts({1: Decimal("-50"), 2: Decimal("40")})

    def test_no_zero_value_settlements(self):
        balances = {1: Decimal("-10"), 2: Decimal("10")}
        for _debtor, _creditor, amount in greedy_settle(balances):
            self.assertGreaterEqual(amount, SETTLEMENT_EPSILON)

    def test_multiple_debtors_multiple_creditors(self):
        balances = {
            1: Decimal("-25"),
            2: Decimal("-25"),
            3: Decimal("20"),
            4: Decimal("30"),
        }
        txns = simplify_debts(balances)
        self.assertTrue(
            verify_settlements(balances, txns, payer_key="from_user", payee_key="to_user")
        )
        self.assertTrue(all(t["amount"] >= SETTLEMENT_EPSILON for t in txns))

    def test_rounding_three_way_split(self):
        # Simulates 100 / 3 drift: 33.33 + 33.33 + 33.34
        balances = {
            "A": Decimal("-33.33"),
            "B": Decimal("-33.33"),
            "C": Decimal("-33.34"),
            "D": Decimal("100.00"),
        }
        payments = greedy_settle(balances)
        settlements = [{"from": d, "to": c, "amount": a} for d, c, a in payments]
        self.assertTrue(
            verify_settlements(balances, settlements, payer_key="from", payee_key="to")
        )

    def test_apply_settlements_reverses_debt_sign(self):
        balances = {1: Decimal("-50"), 2: Decimal("50")}
        txns = [{"from_user": 1, "to_user": 2, "amount": Decimal("50")}]
        after = apply_settlements(balances, txns, payer_key="from_user", payee_key="to_user")
        self.assertLess(abs(after[1]), SETTLEMENT_EPSILON)
        self.assertLess(abs(after[2]), SETTLEMENT_EPSILON)


class GreedySettleEdgeCaseTests(unittest.TestCase):
    def test_single_debtor_single_creditor(self):
        payments = greedy_settle({1: Decimal("-100"), 2: Decimal("100")})
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0][2], Decimal("100.00"))

    def test_negative_and_positive_preserved(self):
        balances = {1: Decimal("-10.50"), 2: Decimal("10.50")}
        payments = greedy_settle(balances)
        self.assertEqual(sum(p[2] for p in payments), Decimal("10.50"))


if __name__ == "__main__":
    unittest.main()
