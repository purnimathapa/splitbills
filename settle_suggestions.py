"""Pairwise balance helpers and settle-up nudge eligibility."""

from __future__ import annotations

from models import (
    PAYMENT_STATUS_PENDING,
    Expense,
    ExpensePaymentLink,
    ExpenseSplit,
    TripMember,
    db,
)

SETTLE_EPSILON = 0.009


def compute_pairwise_net(
    viewer_user_id: int,
    other_user_id: int,
    trip_ids: list[int],
) -> float:
    """Net from the viewer's perspective across trips (positive → other owes viewer)."""
    if not trip_ids or viewer_user_id == other_user_id:
        return 0.0

    net = 0.0
    for trip_id in trip_ids:
        member_rows = TripMember.query.filter_by(trip_id=trip_id).all()
        member_ids = {m.user_id for m in member_rows}
        if viewer_user_id not in member_ids or other_user_id not in member_ids:
            continue

        expenses = Expense.query.filter_by(trip_id=trip_id).all()
        member_count = len(member_ids) or 1

        for expense in expenses:
            amount = expense.amount or 0
            if amount <= 0:
                continue

            splits = ExpenseSplit.query.filter_by(expense_id=expense.id).all()
            split_map = {s.user_id: float(s.amount_owed or 0) for s in splits}

            if split_map:
                owed_viewer = split_map.get(viewer_user_id, 0.0)
                owed_other = split_map.get(other_user_id, 0.0)
                if expense.paid_by == viewer_user_id:
                    net += owed_other
                elif expense.paid_by == other_user_id:
                    net -= owed_viewer
            else:
                share = amount / member_count
                if expense.paid_by == viewer_user_id:
                    net += share
                elif expense.paid_by == other_user_id:
                    net -= share

    return round(net, 2)


def is_within_settle_suggestion(net: float, threshold: float) -> bool:
    amount = abs(net)
    return amount >= SETTLE_EPSILON and amount <= threshold


def find_pending_payment_link(
    debtor_user_id: int,
    creditor_user_id: int,
    trip_ids: list[int],
) -> ExpensePaymentLink | None:
    """Pending guest link for debtor's share on an expense the creditor paid."""
    if not trip_ids:
        return None

    return (
        ExpensePaymentLink.query.join(Expense, ExpensePaymentLink.expense_id == Expense.id)
        .filter(
            ExpensePaymentLink.user_id == debtor_user_id,
            ExpensePaymentLink.status == PAYMENT_STATUS_PENDING,
            Expense.paid_by == creditor_user_id,
            Expense.trip_id.in_(trip_ids),
        )
        .order_by(ExpensePaymentLink.amount_owed.asc())
        .first()
    )


def build_pairwise_suggestion_raw(
    viewer_user_id: int,
    other_user_id: int,
    other_name: str,
    trip_ids: list[int],
    threshold: float,
) -> dict | None:
    net = compute_pairwise_net(viewer_user_id, other_user_id, trip_ids)
    if not is_within_settle_suggestion(net, threshold):
        return None

    return {
        "other_user_id": other_user_id,
        "other_name": other_name,
        "amount": round(abs(net), 2),
        "net": net,
        "viewer_owes": net < 0,
        "trip_ids": trip_ids,
    }
