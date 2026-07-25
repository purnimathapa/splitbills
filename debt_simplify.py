"""Debt simplification for Split Bills.

This module turns net balances into a small list of payment instructions.
It is intentionally free of Flask/SQLAlchemy so you can demo and test the
algorithm on its own.

Convention (matches typical split-bill apps):
    net_balance[user_id] > 0  → user is owed money (creditor, receives)
    net_balance[user_id] < 0  → user owes money (debtor, pays)

Algorithm — greedy two-pointer matching
----------------------------------------
1. Split users into debtors (negative balance) and creditors (positive balance).
2. Sort both lists by amount (largest first) for predictable, explainable steps.
3. Repeatedly match the current debtor to the current creditor:
   transfer min(debt, credit); shrink both; advance pointers when a side hits zero.

Why this minimizes transaction count
--------------------------------------
If k users have non-zero balance and balances sum to zero, at least k - 1 payments
are required (each payment clears at least one person's balance). This greedy
process never needs more than k - 1 payments, so it is optimal.

Floating point
--------------
Amounts are rounded to two decimals; balances within SETTLEMENT_EPSILON are treated
as zero.
"""

from __future__ import annotations

SETTLEMENT_EPSILON = 0.01
MONEY_DECIMALS = 2


def simplify_debts(net_balances: dict[int, float]) -> list[dict[str, int | float]]:
    """Compute settlement transactions that clear all non-zero net balances.

    Args:
        net_balances: Mapping of user_id → net balance after all expenses.
            Positive values mean the user should receive money; negative values
            mean they should pay.

    Returns:
        List of dicts: {"from_user": int, "to_user": int, "amount": float}.
        Empty list when everyone is already settled.

    Raises:
        ValueError: If balances do not sum to approximately zero.
    """
    total = sum(net_balances.values())
    if abs(total) > SETTLEMENT_EPSILON:
        raise ValueError(
            f"Net balances must sum to zero (got {total:.4f}); "
            "cannot settle the group fairly."
        )

    # Debtors pay; store remaining debt as a positive number for clarity.
    debtors: list[list[int | float]] = []
    creditors: list[list[int | float]] = []

    for user_id, balance in net_balances.items():
        if abs(balance) < SETTLEMENT_EPSILON:
            continue
        if balance < 0:
            debtors.append([user_id, abs(balance)])
        else:
            creditors.append([user_id, balance])

    if not debtors and not creditors:
        return []

    # Largest obligations first — easy to walk through on a whiteboard.
    debtors.sort(key=lambda row: row[1], reverse=True)
    creditors.sort(key=lambda row: row[1], reverse=True)

    transactions: list[dict[str, int | float]] = []
    debtor_idx = 0
    creditor_idx = 0

    while debtor_idx < len(debtors) and creditor_idx < len(creditors):
        debtor_id, debt_remaining = debtors[debtor_idx]
        creditor_id, credit_remaining = creditors[creditor_idx]

        payment = min(debt_remaining, credit_remaining)
        payment = round(payment, MONEY_DECIMALS)

        if payment >= SETTLEMENT_EPSILON:
            transactions.append(
                {
                    "from_user": int(debtor_id),
                    "to_user": int(creditor_id),
                    "amount": payment,
                }
            )

        debt_remaining = round(debt_remaining - payment, MONEY_DECIMALS)
        credit_remaining = round(credit_remaining - payment, MONEY_DECIMALS)

        debtors[debtor_idx][1] = debt_remaining
        creditors[creditor_idx][1] = credit_remaining

        if debt_remaining < SETTLEMENT_EPSILON:
            debtor_idx += 1
        if credit_remaining < SETTLEMENT_EPSILON:
            creditor_idx += 1

    return transactions
