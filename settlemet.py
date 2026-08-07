"""Group settlement: net balances from expenses, then debt simplification."""

from __future__ import annotations

from decimal import Decimal

from debt_simplify import greedy_settle
from expense_split_logic import compute_equal_split
from money import MONEY_EPSILON, ZERO, quantize_money


def compute_net_balances(expenses, members) -> dict[str, Decimal]:
    """Net balance per member name (positive = others owe them)."""
    balances: dict[str, Decimal] = {member.name: ZERO for member in members}
    member_by_id = {member.id: member for member in members}
    member_ids = [member.id for member in members]

    for expense in expenses:
        amount = quantize_money(expense.amount or ZERO)
        if amount <= ZERO:
            continue

        payer = expense.payer or member_by_id.get(expense.paid_by)
        if payer is None:
            continue
        balances[payer.name] += amount

        splits = expense.splits if expense.splits is not None else []
        if splits:
            for split in splits:
                user = split.user or member_by_id.get(split.user_id)
                if user is None:
                    continue
                balances[user.name] -= quantize_money(split.amount_owed or ZERO)
        elif member_ids:
            # Equal split with cent drift fixed per expense (sum of shares == amount).
            owed_by_id = compute_equal_split(amount, member_ids)
            for member in members:
                share = quantize_money(owed_by_id.get(member.id, ZERO))
                balances[member.name] -= share

    return {name: quantize_money(value) for name, value in balances.items()}


def calculate_settlement(expenses, members):
    """Net balances from expenses, then greedy payment matching."""
    if not members:
        return []

    balances = compute_net_balances(expenses, members)
    payments = greedy_settle(balances)

    return [
        {"from": debtor, "to": creditor, "amount": amount}
        for debtor, creditor, amount in payments
        if amount >= MONEY_EPSILON
    ]
