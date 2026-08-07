"""Pairwise balance helpers and settle-up nudge eligibility."""

from __future__ import annotations

from decimal import Decimal

from models import (
    PAYMENT_STATUS_PENDING,
    Expense,
    ExpensePaymentLink,
    ExpenseSplit,
    TripMember,
    db,
)
from money import MONEY_EPSILON, ZERO, quantize_money, to_decimal

SETTLE_EPSILON = MONEY_EPSILON


def pairwise_net_on_expense(
    *,
    amount: Decimal | float,
    paid_by: int,
    viewer_user_id: int,
    other_user_id: int,
    split_map: dict[int, Decimal | float],
    member_count: int,
) -> Decimal:
    """Viewer-centric net from a single expense vs one other participant."""
    total = quantize_money(amount)
    if total <= ZERO:
        return ZERO
    if split_map:
        owed_viewer = quantize_money(split_map.get(viewer_user_id, ZERO))
        owed_other = quantize_money(split_map.get(other_user_id, ZERO))
        if paid_by == viewer_user_id:
            return owed_other
        if paid_by == other_user_id:
            return -owed_viewer
        return ZERO
    share = quantize_money(total / member_count)
    if paid_by == viewer_user_id:
        return share
    if paid_by == other_user_id:
        return -share
    return ZERO


def compute_pairwise_net(
    viewer_user_id: int,
    other_user_id: int,
    trip_ids: list[int],
) -> Decimal:
    """Net from the viewer's perspective across trips (positive → other owes viewer)."""
    if viewer_user_id == other_user_id:
        return ZERO

    net = ZERO
    if trip_ids:
        for trip_id in trip_ids:
            member_rows = TripMember.query.filter_by(trip_id=trip_id).all()
            member_ids = {m.user_id for m in member_rows}
            if viewer_user_id not in member_ids or other_user_id not in member_ids:
                continue

            expenses = Expense.query.filter_by(trip_id=trip_id).all()
            member_count = len(member_ids) or 1

            for expense in expenses:
                amount = quantize_money(expense.amount or ZERO)
                if amount <= ZERO:
                    continue

                splits = ExpenseSplit.query.filter_by(expense_id=expense.id).all()
                split_map = {
                    s.user_id: quantize_money(s.amount_owed or ZERO) for s in splits
                }
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
    return quantize_money(net)


def is_within_settle_suggestion(net: Decimal | float, threshold: Decimal | float) -> bool:
    amount = abs(to_decimal(net))
    return amount >= SETTLE_EPSILON and amount <= to_decimal(threshold)


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
        "amount": quantize_money(abs(net)),
        "net": net,
        "viewer_owes": net < 0,
        "trip_ids": trip_ids,
    }


def enrich_settle_suggestion(raw: dict, *, viewer_id: int) -> dict:
    """Attach button URL/label for a raw pairwise suggestion."""
    from flask import url_for

    from services.guest_payments import build_guest_payment_url_for_link

    trip_ids = raw["trip_ids"]
    other_id = raw["other_user_id"]
    trip_id = trip_ids[0] if trip_ids else None

    if raw["viewer_owes"]:
        link = find_pending_payment_link(viewer_id, other_id, trip_ids)
        raw["button_label"] = "Pay your share"
        if link:
            raw["button_url"] = build_guest_payment_url_for_link(link)
            raw["payment_link_id"] = link.id
        elif trip_id:
            raw["button_url"] = url_for("settlement", trip_id=trip_id)
        else:
            raw["button_url"] = url_for("collect")
    else:
        link = find_pending_payment_link(other_id, viewer_id, trip_ids)
        raw["button_label"] = "Open payment link"
        if link:
            raw["button_url"] = url_for("collect", highlight=link.id)
            raw["payment_link_id"] = link.id
            raw["copy_url"] = build_guest_payment_url_for_link(link)
        elif trip_id:
            raw["button_url"] = url_for("settlement", trip_id=trip_id)
        else:
            raw["button_url"] = url_for("collect")

    return raw


def settle_suggestion_for_friend(friend, shared_trip_ids: list[int], *, viewer_id: int) -> dict | None:
    from flask import current_app

    threshold = current_app.config["SETTLE_SUGGESTION_THRESHOLD"]
    raw = build_pairwise_suggestion_raw(
        viewer_id,
        friend.id,
        friend.name,
        shared_trip_ids,
        threshold,
    )
    if raw is None:
        return None
    return enrich_settle_suggestion(raw, viewer_id=viewer_id)


def settle_suggestions_for_trip(trip_id: int, members: list, *, viewer_id: int) -> list[dict]:
    from flask import current_app

    threshold = current_app.config["SETTLE_SUGGESTION_THRESHOLD"]
    trip_ids = [trip_id]
    out = []
    for member in members:
        if member.id == viewer_id:
            continue
        raw = build_pairwise_suggestion_raw(
            viewer_id,
            member.id,
            member.name,
            trip_ids,
            threshold,
        )
        if raw:
            out.append(enrich_settle_suggestion(raw, viewer_id=viewer_id))
    return out
