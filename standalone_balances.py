"""Settlement and pairwise balance for one-off (non–split-group) expenses."""

from __future__ import annotations

from sqlalchemy import or_

from expense_participants import get_expense_member_ids, get_expense_members
from models import Expense, ExpenseParticipant, User, db
from settlemet import calculate_settlement
from settle_suggestions import pairwise_net_on_expense


def standalone_expenses_for_user(user_id: int) -> list[Expense]:
    participant_rows = (
        db.session.query(ExpenseParticipant.expense_id)
        .filter(ExpenseParticipant.user_id == user_id)
        .all()
    )
    participant_expense_ids = [row[0] for row in participant_rows]
    filters = [Expense.paid_by == user_id]
    if participant_expense_ids:
        filters.append(Expense.id.in_(participant_expense_ids))
    return (
        Expense.query.filter(Expense.trip_id.is_(None), or_(*filters))
        .order_by(Expense.created_at.desc())
        .all()
    )


def standalone_expenses_between_users(
    user_a_id: int,
    user_b_id: int,
) -> list[Expense]:
    if user_a_id == user_b_id:
        return []
    out: list[Expense] = []
    for expense in standalone_expenses_for_user(user_a_id):
        member_ids = set(get_expense_member_ids(expense))
        if user_b_id in member_ids:
            out.append(expense)
    return out


def friend_user_ids_from_standalone(user_id: int) -> set[int]:
    ids: set[int] = set()
    for expense in standalone_expenses_for_user(user_id):
        for member_id in get_expense_member_ids(expense):
            if member_id != user_id:
                ids.add(member_id)
    return ids


def net_delta_from_settlements(settlements: list[dict], user_name: str) -> float:
    delta = 0.0
    for row in settlements:
        if row["from"] == user_name:
            delta -= row["amount"]
        elif row["to"] == user_name:
            delta += row["amount"]
    return delta


def net_balance_from_standalone(user_id: int) -> float:
    user = User.query.get(user_id)
    if not user:
        return 0.0
    net = 0.0
    for expense in standalone_expenses_for_user(user_id):
        members = get_expense_members(expense)
        if len(members) < 2:
            continue
        settlements = calculate_settlement([expense], members)
        net += net_delta_from_settlements(settlements, user.name)
    return round(net, 2)


def apply_standalone_to_pairwise_balances(
    viewer_user_id: int,
    viewer_name: str,
    net_by_name: dict[str, float],
) -> None:
    """Mutate ``net_by_name`` (friend name → net) with one-off expenses."""
    for expense in standalone_expenses_for_user(viewer_user_id):
        member_ids = get_expense_member_ids(expense)
        if viewer_user_id not in member_ids:
            continue
        members = get_expense_members(expense)
        member_count = len(members) or 1
        splits = {s.user_id: float(s.amount_owed or 0) for s in expense.splits or []}
        for member in members:
            if member.id == viewer_user_id:
                continue
            delta = pairwise_net_on_expense(
                amount=expense.amount or 0,
                paid_by=expense.paid_by,
                viewer_user_id=viewer_user_id,
                other_user_id=member.id,
                split_map=splits,
                member_count=member_count,
            )
            if abs(delta) < 0.009:
                continue
            net_by_name[member.name] = net_by_name.get(member.name, 0.0) + delta


def compute_pairwise_net_standalone(viewer_user_id: int, other_user_id: int) -> float:
    if viewer_user_id == other_user_id:
        return 0.0
    net = 0.0
    for expense in standalone_expenses_between_users(viewer_user_id, other_user_id):
        member_ids = get_expense_member_ids(expense)
        member_count = len(member_ids) or 1
        splits = {s.user_id: float(s.amount_owed or 0) for s in expense.splits or []}
        net += pairwise_net_on_expense(
            amount=expense.amount or 0,
            paid_by=expense.paid_by,
            viewer_user_id=viewer_user_id,
            other_user_id=other_user_id,
            split_map=splits,
            member_count=member_count,
        )
    return round(net, 2)
