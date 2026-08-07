"""Tests for settlemet.calculate_settlement and compute_net_balances."""

import unittest
from decimal import Decimal
from types import SimpleNamespace

from debt_simplify import SETTLEMENT_EPSILON, verify_settlements
from settlemet import calculate_settlement, compute_net_balances


def _member(uid: int, name: str):
    return SimpleNamespace(id=uid, name=name)


def _expense(*, amount, paid_by, payer_name, splits=None):
    payer = SimpleNamespace(id=paid_by, name=payer_name)
    split_objs = []
    if splits:
        for user_id, owed, user_name in splits:
            split_objs.append(
                SimpleNamespace(
                    user_id=user_id,
                    amount_owed=Decimal(str(owed)),
                    user=SimpleNamespace(id=user_id, name=user_name),
                )
            )
    return SimpleNamespace(
        amount=Decimal(str(amount)),
        paid_by=paid_by,
        payer=payer,
        splits=split_objs,
    )


class ComputeNetBalancesTests(unittest.TestCase):
    def test_equal_split_without_stored_splits(self):
        members = [_member(1, "Alice"), _member(2, "Bob"), _member(3, "Carol")]
        expenses = [_expense(amount="100", paid_by=1, payer_name="Alice", splits=[])]
        balances = compute_net_balances(expenses, members)
        # Payer credited 100, each member debited equal share (with cent drift fix).
        self.assertEqual(sum(balances.values()), Decimal("0.00"))
        self.assertEqual(balances["Bob"], Decimal("-33.33"))
        self.assertEqual(balances["Carol"], Decimal("-33.33"))
        self.assertEqual(balances["Alice"], Decimal("66.66"))

    def test_explicit_splits(self):
        members = [_member(1, "Alice"), _member(2, "Bob")]
        expenses = [
            _expense(
                amount="50",
                paid_by=1,
                payer_name="Alice",
                splits=[(2, "50", "Bob")],
            )
        ]
        balances = compute_net_balances(expenses, members)
        # Alice paid; only Bob has a split row → Alice is owed the full amount.
        self.assertEqual(balances["Alice"], Decimal("50.00"))
        self.assertEqual(balances["Bob"], Decimal("-50.00"))


class CalculateSettlementTests(unittest.TestCase):
    def test_two_person_settlement(self):
        members = [_member(1, "Alice"), _member(2, "Bob")]
        expenses = [
            _expense(
                amount="80",
                paid_by=1,
                payer_name="Alice",
                splits=[(2, "80", "Bob")],
            )
        ]
        settlements = calculate_settlement(expenses, members)
        self.assertEqual(len(settlements), 1)
        self.assertEqual(settlements[0]["from"], "Bob")
        self.assertEqual(settlements[0]["to"], "Alice")
        self.assertEqual(settlements[0]["amount"], Decimal("80.00"))

        balances = compute_net_balances(expenses, members)
        self.assertTrue(
            verify_settlements(balances, settlements, payer_key="from", payee_key="to")
        )

    def test_three_person_minimizes_payments(self):
        members = [_member(1, "A"), _member(2, "B"), _member(3, "C")]
        expenses = [
            _expense(amount="90", paid_by=3, payer_name="C", splits=[]),
        ]
        settlements = calculate_settlement(expenses, members)
        balances = compute_net_balances(expenses, members)

        self.assertLessEqual(len(settlements), 2)
        self.assertTrue(
            verify_settlements(balances, settlements, payer_key="from", payee_key="to")
        )
        self.assertTrue(all(s["amount"] >= SETTLEMENT_EPSILON for s in settlements))

    def test_empty_when_everyone_settled(self):
        members = [_member(1, "A"), _member(2, "B")]
        settlements = calculate_settlement([], members)
        self.assertEqual(settlements, [])

    def test_zero_amount_expense_ignored(self):
        members = [_member(1, "A"), _member(2, "B")]
        expenses = [_expense(amount="0", paid_by=1, payer_name="A", splits=[])]
        balances = compute_net_balances(expenses, members)
        self.assertEqual(balances["A"], Decimal("0.00"))
        self.assertEqual(balances["B"], Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
