from collections import defaultdict

from flask import current_app, session
from flask_login import current_user

from models import (
    PAYMENT_STATUS_PAID,
    Expense,
    ExpensePaymentLink,
    ExpenseSplit,
    Trip,
    TripMember,
    User,
    db,
)
from services.guest_payments import (
    build_guest_claim_url_for_link,
    build_guest_payment_url_for_link,
)
from money import MONEY_EPSILON, ZERO, quantize_money
from settlemet import calculate_settlement
from standalone_balances import (
    apply_standalone_to_pairwise_balances,
    friend_user_ids_from_standalone,
    net_balance_from_standalone,
    standalone_expenses_between_users,
)


def guest_link_url_for_row(link: ExpensePaymentLink, expense: Expense | None) -> str:
    if expense and getattr(expense, "self_service_items", False) and not expense.claims_finalized_at:
        return build_guest_claim_url_for_link(link)
    return build_guest_payment_url_for_link(link)


def get_payment_hub_for_user(user_id: int) -> dict:
    """Pending/collected guest links for expenses this user paid."""
    paid_expense_ids = [
        row[0]
        for row in db.session.query(Expense.id)
        .filter(Expense.paid_by == user_id)
        .all()
    ]
    if not paid_expense_ids:
        return {
            "pending_links": [],
            "collected_links": [],
            "pending_total": 0.0,
            "collected_total": 0.0,
            "pending_count": 0,
        }

    links = (
        ExpensePaymentLink.query.filter(
            ExpensePaymentLink.expense_id.in_(paid_expense_ids)
        )
        .order_by(ExpensePaymentLink.created_at.desc())
        .all()
    )
    pending = []
    collected = []
    pending_total = 0.0
    collected_total = 0.0
    for link in links:
        expense = link.expense
        guest = link.user
        trip = Trip.query.get(expense.trip_id) if expense else None
        row = {
            "link": link,
            "expense": expense,
            "guest": guest,
            "trip": trip,
            "url": guest_link_url_for_row(link, expense),
            "is_claim_link": bool(
                expense
                and getattr(expense, "self_service_items", False)
                and not expense.claims_finalized_at
            ),
        }
        if link.status == PAYMENT_STATUS_PAID:
            collected.append(row)
            collected_total += quantize_money(link.amount_owed or ZERO)
        else:
            pending.append(row)
            pending_total += quantize_money(link.amount_owed or ZERO)

    return {
        "pending_links": pending,
        "collected_links": collected,
        "pending_total": quantize_money(pending_total),
        "collected_total": quantize_money(collected_total),
        "pending_count": len(pending),
    }


def get_user_net_balance(user_id: int) -> float:
    """Net settlement balance across all groups (positive = others owe you)."""
    from services.trip_access import get_user_trips

    trips = get_user_trips()
    net = 0.0
    for trip in trips:
        expenses = Expense.query.filter_by(trip_id=trip.id).all()
        if not expenses:
            continue
        memberships = TripMember.query.filter_by(trip_id=trip.id).all()
        member_ids = [m.user_id for m in memberships]
        members = User.query.filter(User.id.in_(member_ids)).all()
        user = User.query.get(user_id)
        if not user or user not in members:
            continue
        settlements = calculate_settlement(expenses, members)
        for s in settlements:
            if s["from"] == user.name:
                net -= s["amount"]
            elif s["to"] == user.name:
                net += s["amount"]
    net += float(net_balance_from_standalone(user_id))
    return float(quantize_money(net))


def get_all_friends():
    """Get all unique friends across all trips for the current user."""
    from services.trip_access import get_user_trips

    trips = get_user_trips()
    friend_ids = set()
    for trip in trips:
        members = TripMember.query.filter_by(trip_id=trip.id).all()
        for m in members:
            if m.user_id != current_user.id:
                friend_ids.add(m.user_id)
    friend_ids.update(friend_user_ids_from_standalone(current_user.id))
    if not friend_ids:
        return []
    return User.query.filter(User.id.in_(friend_ids)).order_by(User.name).all()


def get_global_settlements():
    """Calculate net settlements across all trips for current user."""
    from services.trip_access import get_user_trips

    trips = get_user_trips()
    net_balance = defaultdict(float)

    for trip in trips:
        memberships = TripMember.query.filter_by(trip_id=trip.id).all()
        member_ids = [m.user_id for m in memberships]
        members = User.query.filter(User.id.in_(member_ids)).order_by(User.name).all()
        expenses = Expense.query.filter_by(trip_id=trip.id).all()

        if not members or not expenses:
            continue

        settlements = calculate_settlement(expenses, members)
        for s in settlements:
            if s["from"] == current_user.name:
                net_balance[s["to"]] -= s["amount"]
            elif s["to"] == current_user.name:
                net_balance[s["from"]] += s["amount"]

    apply_standalone_to_pairwise_balances(
        current_user.id,
        current_user.name,
        net_balance,
    )

    return dict(net_balance)


def get_split_friend_candidates():
    """Registered friends you can optionally add to a one-off receipt split."""
    ids: set[int] = set()
    ids.update(friend_user_ids_from_standalone(current_user.id))
    for friend in get_all_friends():
        ids.add(friend.id)
    ids.discard(current_user.id)
    if not ids:
        return []
    return User.query.filter(User.id.in_(ids)).order_by(User.name).all()


def build_expense_summaries(expenses, viewer_id: int) -> dict[int, dict]:
    """Splitwise-style one-line summaries per expense for the list view."""
    if not expenses:
        return {}
    expense_ids = [e.id for e in expenses]
    splits_by_expense: dict[int, list] = defaultdict(list)
    for split in ExpenseSplit.query.filter(ExpenseSplit.expense_id.in_(expense_ids)).all():
        splits_by_expense[split.expense_id].append(split)

    user_ids: set[int] = set()
    for expense in expenses:
        user_ids.add(expense.paid_by)
        for split in splits_by_expense.get(expense.id, []):
            user_ids.add(split.user_id)
    users = {
        u.id: u
        for u in User.query.filter(User.id.in_(user_ids)).all()
    } if user_ids else {}

    from money import quantize_money, to_decimal

    out: dict[int, dict] = {}
    conv = to_decimal(session.get("conversion_rate", 1))
    cur = session.get("currency", current_app.config.get("DEFAULT_CURRENCY", "Rs"))

    def _display(value) -> float:
        return float(quantize_money(to_decimal(value or 0) * conv))

    for expense in expenses:
        splits = splits_by_expense.get(expense.id, [])
        payer = users.get(expense.paid_by)
        amount = expense.amount or 0
        paid_line = ""
        secondary: list[dict] = []

        if expense.paid_by == viewer_id:
            paid_line = f"you paid {cur} {_display(amount)}"
            for split in splits:
                if split.user_id == viewer_id or (split.amount_owed or 0) <= 0:
                    continue
                friend = users.get(split.user_id)
                fname = (friend.name.split()[0] if friend and friend.name else "friend")
                secondary.append(
                    {
                        "text": f"you lent {fname} {cur} {_display(split.amount_owed)}",
                        "tone": "positive",
                    }
                )
        else:
            viewer_split = next((s for s in splits if s.user_id == viewer_id), None)
            owed = (viewer_split.amount_owed or 0) if viewer_split else 0
            if owed > 0 and payer:
                pname = payer.name.split()[0] if payer.name else "someone"
                secondary.append(
                    {
                        "text": f"you owe {pname} {cur} {_display(owed)}",
                        "tone": "negative",
                    }
                )
            elif payer:
                pname = payer.name.split()[0] if payer.name else "someone"
                paid_line = f"{pname} paid {cur} {_display(amount)}"

        out[expense.id] = {"paid_line": paid_line, "secondary_lines": secondary}
    return out
