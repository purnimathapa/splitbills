"""Activity feed logging and queries."""

from __future__ import annotations

from flask_sqlalchemy.pagination import Pagination
from sqlalchemy import or_

from models import (
    ACTION_EXPENSE_CREATED,
    ACTION_MEMBER_JOINED,
    ACTION_PAYMENT_CONFIRMED,
    ActivityLog,
    Expense,
    ExpensePaymentLink,
    User,
    db,
)

ACTIVITY_ICONS = {
    ACTION_EXPENSE_CREATED: "🧾",
    ACTION_PAYMENT_CONFIRMED: "✓",
    ACTION_MEMBER_JOINED: "👋",
}


def log_activity(
    actor_user_id: int,
    action_type: str,
    description: str,
    *,
    trip_id: int | None = None,
    related_expense_id: int | None = None,
) -> ActivityLog:
    entry = ActivityLog(
        trip_id=trip_id,
        actor_user_id=actor_user_id,
        action_type=action_type,
        description=description,
        related_expense_id=related_expense_id,
    )
    db.session.add(entry)
    return entry


def log_expense_created(expense: Expense, actor_user_id: int) -> ActivityLog:
    amount = expense.amount or 0
    text = f'Added “{expense.description}” for {amount:.2f}'
    return log_activity(
        actor_user_id,
        ACTION_EXPENSE_CREATED,
        text,
        trip_id=expense.trip_id,
        related_expense_id=expense.id,
    )


def log_member_joined(trip_id: int, actor_user_id: int, trip_name: str) -> ActivityLog:
    user = User.query.get(actor_user_id)
    name = user.name if user else "Someone"
    return log_activity(
        actor_user_id,
        ACTION_MEMBER_JOINED,
        f"{name} joined {trip_name}",
        trip_id=trip_id,
    )


def log_payment_confirmed(link: ExpensePaymentLink, provider: str) -> ActivityLog | None:
    expense = link.expense
    guest = link.user
    if expense is None or guest is None:
        return None
    provider_label = provider.replace("_", " ").title()
    text = (
        f"{guest.name} paid {link.amount_owed:.2f} for “{expense.description}” "
        f"({provider_label})"
    )
    return log_activity(
        guest.id,
        ACTION_PAYMENT_CONFIRMED,
        text,
        trip_id=expense.trip_id,
        related_expense_id=expense.id,
    )


def activity_query_for_trip_ids(trip_ids: list[int]):
    q = ActivityLog.query
    if trip_ids:
        q = q.filter(ActivityLog.trip_id.in_(trip_ids))
    else:
        q = q.filter(ActivityLog.id < 0)
    return q.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())


def paginate_activity(
    trip_ids: list[int] | None = None,
    *,
    trip_id: int | None = None,
    page: int = 1,
    per_page: int = 20,
) -> Pagination:
    if trip_id is not None:
        q = ActivityLog.query.filter_by(trip_id=trip_id).order_by(
            ActivityLog.created_at.desc(),
            ActivityLog.id.desc(),
        )
    else:
        q = activity_query_for_trip_ids(trip_ids or [])

    return q.paginate(page=page, per_page=per_page, error_out=False)


def paginate_activity_for_user(
    user_id: int,
    trip_ids: list[int],
    *,
    page: int = 1,
    per_page: int = 20,
) -> Pagination:
    from expense_participants import visible_expense_ids_for_user

    expense_ids = visible_expense_ids_for_user(user_id, trip_ids)
    conditions = []
    if trip_ids:
        conditions.append(ActivityLog.trip_id.in_(trip_ids))
    if expense_ids:
        conditions.append(ActivityLog.related_expense_id.in_(expense_ids))
    if not conditions:
        q = ActivityLog.query.filter(ActivityLog.id < 0)
    else:
        q = ActivityLog.query.filter(or_(*conditions))
    q = q.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
    return q.paginate(page=page, per_page=per_page, error_out=False)


def recent_activity_for_user(
    user_id: int,
    trip_ids: list[int],
    *,
    limit: int = 8,
) -> list[ActivityLog]:
    """Activity in the user's split groups plus one-off expenses they're on."""
    from expense_participants import visible_expense_ids_for_user

    expense_ids = visible_expense_ids_for_user(user_id, trip_ids)
    conditions = []
    if trip_ids:
        conditions.append(ActivityLog.trip_id.in_(trip_ids))
    if expense_ids:
        conditions.append(ActivityLog.related_expense_id.in_(expense_ids))
    if not conditions:
        return []
    return (
        ActivityLog.query.filter(or_(*conditions))
        .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
        .limit(limit)
        .all()
    )


def recent_activity(
    trip_ids: list[int] | None = None,
    *,
    trip_id: int | None = None,
    limit: int = 8,
) -> list[ActivityLog]:
    if trip_id is not None:
        q = ActivityLog.query.filter_by(trip_id=trip_id)
    else:
        q = activity_query_for_trip_ids(trip_ids or [])
    return q.limit(limit).all()
