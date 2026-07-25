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


def pairwise_net_on_expense(
    *,
    amount: float,
    paid_by: int,
    viewer_user_id: int,
    other_user_id: int,
    split_map: dict[int, float],
    member_count: int,
) -> float:
    """Viewer-centric net from a single expense vs one other participant."""
    if amount <= 0:
        return 0.0
    if split_map:
        owed_viewer = split_map.get(viewer_user_id, 0.0)
        owed_other = split_map.get(other_user_id, 0.0)
        if paid_by == viewer_user_id:
            return owed_other
        if paid_by == other_user_id:
            return -owed_viewer
        return 0.0
    share = amount / member_count
    if paid_by == viewer_user_id:
        return share
    if paid_by == other_user_id:
        return -share
    return 0.0


def compute_pairwise_net(
    viewer_user_id: int,
    other_user_id: int,
    trip_ids: list[int],
) -> float:
    """Net from the viewer's perspective across trips (positive → other owes viewer)."""
    if viewer_user_id == other_user_id:
        return 0.0

    net = 0.0
    if trip_ids:
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
                net += pairwise_net_on_expense(
                    amount=amount,
                    paid_by=expense.paid_by,
                    viewer_user_id=viewer_user_id,
                    other_user_id=other_user_id,
                    split_map=split_map,
                    member_count=member_count,
                )

    from standalone_balances import compute_pairwise_net_standalone

    net += compute_pairwise_net_standalone(viewer_user_id, other_user_id)
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
    base = (
        ExpensePaymentLink.query.join(Expense, ExpensePaymentLink.expense_id == Expense.id)
        .filter(
            ExpensePaymentLink.user_id == debtor_user_id,
            ExpensePaymentLink.status == PAYMENT_STATUS_PENDING,
            Expense.paid_by == creditor_user_id,
        )
        .order_by(ExpensePaymentLink.amount_owed.asc())
    )
    standalone = base.filter(Expense.trip_id.is_(None)).first()
    if standalone:
        return standalone
    if not trip_ids:
        return None
    return base.filter(Expense.trip_id.in_(trip_ids)).first()


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
