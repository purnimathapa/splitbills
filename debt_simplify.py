"""Debt simplification for Split Bills.

Convention (matches split-bill apps):
    net_balance[key] > 0  → this person is owed money (creditor)
    net_balance[key] < 0  → this person owes money (debtor)

Algorithm — greedy two-pointer matching
----------------------------------------
1. Split people into debtors (negative balance) and creditors (positive balance).
2. Sort both lists by amount, largest first.
3. Match the current debtor to the current creditor:
   pay min(debt, credit); shrink both; move on when a side reaches zero.

Why this works
--------------
If k people have non-zero balance and balances sum to zero, at least k−1
payments are required. Each step clears at least one person, so we never
need more than k−1 payments — this greedy approach is optimal.

Complexity: O(n log n) time for sorting, O(n) space for the two lists
(n = people with non-zero balance).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TypeVar

from money import MONEY_EPSILON, ZERO, quantize_money, to_decimal

K = TypeVar("K")

SETTLEMENT_EPSILON = MONEY_EPSILON


def _assert_balances_sum_to_zero(balances: dict[K, Decimal]) -> None:
    total = sum(balances.values(), ZERO)
    if abs(total) > SETTLEMENT_EPSILON:
        raise ValueError(
            f"Net balances must sum to zero (got {total}); "
            "cannot settle the group fairly."
        )


def greedy_settle(
    net_balances: dict[K, Decimal | float | int | str],
) -> list[tuple[K, K, Decimal]]:
    """Return (debtor, creditor, amount) payments that clear all balances."""
    normalized = {key: quantize_money(value) for key, value in net_balances.items()}
    _assert_balances_sum_to_zero(normalized)

    debtors: list[list[K | Decimal]] = []
    creditors: list[list[K | Decimal]] = []

    for key, balance in normalized.items():
        if abs(balance) < SETTLEMENT_EPSILON:
            continue
        if balance < ZERO:
            debtors.append([key, abs(balance)])
        else:
            creditors.append([key, balance])

    if not debtors and not creditors:
        return []

    debtors.sort(key=lambda row: row[1], reverse=True)
    creditors.sort(key=lambda row: row[1], reverse=True)

    payments: list[tuple[K, K, Decimal]] = []
    debtor_idx = 0
    creditor_idx = 0

    while debtor_idx < len(debtors) and creditor_idx < len(creditors):
        debtor_key, debt_remaining = debtors[debtor_idx]
        creditor_key, credit_remaining = creditors[creditor_idx]

        payment = quantize_money(min(debt_remaining, credit_remaining))
        if payment >= SETTLEMENT_EPSILON:
            payments.append((debtor_key, creditor_key, payment))

        debt_remaining = quantize_money(debt_remaining - payment)
        credit_remaining = quantize_money(credit_remaining - payment)

        debtors[debtor_idx][1] = debt_remaining
        creditors[creditor_idx][1] = credit_remaining

        if debt_remaining < SETTLEMENT_EPSILON:
            debtor_idx += 1
        if credit_remaining < SETTLEMENT_EPSILON:
            creditor_idx += 1

    return payments


def simplify_debts(
    net_balances: dict[int, float | Decimal],
) -> list[dict[str, int | Decimal]]:
    """Compute settlement transactions keyed by user id."""
    return [
        {"from_user": debtor, "to_user": creditor, "amount": amount}
        for debtor, creditor, amount in greedy_settle(net_balances)
    ]


def apply_settlements(
    initial_balances: dict[K, Decimal | float | int | str],
    settlements: list[dict],
    *,
    payer_key: str,
    payee_key: str,
    amount_key: str = "amount",
) -> dict[K, Decimal]:
    """Simulate payments: debtor balance moves toward zero, creditor toward zero."""
    remaining = {key: quantize_money(value) for key, value in initial_balances.items()}
    for settlement in settlements:
        amount = quantize_money(settlement[amount_key])
        if amount < SETTLEMENT_EPSILON:
            continue
        payer = settlement[payer_key]
        payee = settlement[payee_key]
        remaining[payer] = quantize_money(remaining.get(payer, ZERO) + amount)
        remaining[payee] = quantize_money(remaining.get(payee, ZERO) - amount)
    return remaining


def verify_settlements(
    initial_balances: dict[K, Decimal | float | int | str],
    settlements: list[dict],
    *,
    payer_key: str,
    payee_key: str,
    amount_key: str = "amount",
) -> bool:
    """True when every participant's balance is zero after applying settlements."""
    remaining = apply_settlements(
        initial_balances,
        settlements,
        payer_key=payer_key,
        payee_key=payee_key,
        amount_key=amount_key,
    )
    return all(abs(balance) < SETTLEMENT_EPSILON for balance in remaining.values())


def settlements_clear_balances(
    initial_balances: dict[K, Decimal | float | int | str],
    settlements: list[dict],
    *,
    payer_key: str,
    payee_key: str,
    amount_key: str = "amount",
) -> tuple[bool, dict[K, Decimal]]:
    """Return (all_cleared, remaining_balances) for debugging and tests."""
    remaining = apply_settlements(
        initial_balances,
        settlements,
        payer_key=payer_key,
        payee_key=payee_key,
        amount_key=amount_key,
    )
    cleared = all(abs(balance) < SETTLEMENT_EPSILON for balance in remaining.values())
    return cleared, remaining
