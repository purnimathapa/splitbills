"""In-app notification bell — create, dedupe, and query user alerts."""

from __future__ import annotations

from datetime import datetime

from flask import url_for
from sqlalchemy.exc import IntegrityError

from money import MONEY_EPSILON, quantize_money

from models import (
    NOTIFICATION_EXPENSE_ADDED,
    NOTIFICATION_EXPENSE_UPDATED,
    NOTIFICATION_GROUP_ADDED,
    NOTIFICATION_PAYMENT_RECEIVED,
    NOTIFICATION_RECURRING_GENERATED,
    NOTIFICATION_REMINDER_SENT,
    NOTIFICATION_SETTLEMENT_COMPLETED,
    NOTIFICATION_SETTLEMENT_REQUESTED,
    NOTIFICATION_TRIP_JOIN,
    Expense,
    ExpenseParticipant,
    ExpensePaymentLink,
    Notification,
    TripMember,
    User,
    db,
)


def create_notification(
    user_id: int,
    message: str,
    *,
    kind: str,
    href: str | None = None,
    dedupe_key: str | None = None,
) -> Notification | None:
    """Create a notification. Skips insert when dedupe_key already exists for the user."""
    if dedupe_key:
        existing = Notification.query.filter_by(
            user_id=user_id,
            dedupe_key=dedupe_key,
        ).first()
        if existing is not None:
            return None

    note = Notification(
        user_id=user_id,
        kind=kind,
        message=message,
        href=href,
        dedupe_key=dedupe_key,
    )
    db.session.add(note)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.expunge(note)
        return None
    return note


def unread_count(user_id: int) -> int:
    return Notification.query.filter_by(user_id=user_id, read_at=None).count()


def recent_notifications(user_id: int, limit: int = 8) -> list[Notification]:
    return (
        Notification.query.filter_by(user_id=user_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .all()
    )


def get_notification_for_user(notification_id: int, user_id: int) -> Notification | None:
    return Notification.query.filter_by(id=notification_id, user_id=user_id).first()


def mark_all_read(user_id: int) -> int:
    now = datetime.utcnow()
    return Notification.query.filter_by(user_id=user_id, read_at=None).update(
        {Notification.read_at: now},
        synchronize_session=False,
    )


def mark_read(notification_id: int, user_id: int) -> bool:
    note = get_notification_for_user(notification_id, user_id)
    if note is None:
        return False
    if note.read_at is None:
        note.read_at = datetime.utcnow()
    return True


def _expense_href(expense: Expense) -> str | None:
    try:
        if expense.trip_id:
            return url_for(
                "expense_detail",
                trip_id=expense.trip_id,
                expense_id=expense.id,
            )
        return url_for("expense_detail_standalone", expense_id=expense.id)
    except RuntimeError:
        if expense.trip_id:
            return f"/groups/{expense.trip_id}/expenses/{expense.id}"
        return f"/expenses/{expense.id}"


def _expense_recipient_ids(expense: Expense) -> set[int]:
    recipient_ids: set[int] = set()
    if expense.trip_id:
        rows = TripMember.query.filter_by(trip_id=expense.trip_id).all()
        recipient_ids.update(row.user_id for row in rows)
    else:
        participant_rows = ExpenseParticipant.query.filter_by(expense_id=expense.id).all()
        recipient_ids.update(row.user_id for row in participant_rows)
    for split in expense.splits:
        recipient_ids.add(split.user_id)
    return recipient_ids


def _actor_name(actor_user_id: int) -> str:
    actor = db.session.get(User, actor_user_id)
    return actor.name if actor else "Someone"


def notify_user_added_to_group(user_id: int, trip_id: int, trip_name: str) -> None:
    try:
        href = url_for("group_details", trip_id=trip_id)
    except RuntimeError:
        href = f"/groups/{trip_id}"
    create_notification(
        user_id,
        f"You joined {trip_name}",
        kind=NOTIFICATION_GROUP_ADDED,
        href=href,
        dedupe_key=f"group_added:{trip_id}:{user_id}",
    )


def notify_trip_members_of_join(trip_id: int, actor_user_id: int, trip_name: str) -> None:
    actor_name = _actor_name(actor_user_id)
    try:
        href = url_for("group_details", trip_id=trip_id)
    except RuntimeError:
        href = f"/groups/{trip_id}"
    memberships = TripMember.query.filter_by(trip_id=trip_id).all()
    for membership in memberships:
        if membership.user_id == actor_user_id:
            continue
        create_notification(
            membership.user_id,
            f"{actor_name} joined {trip_name}",
            kind=NOTIFICATION_TRIP_JOIN,
            href=href,
            dedupe_key=f"trip_join:{trip_id}:{actor_user_id}:{membership.user_id}",
        )


def notify_expense_added(expense: Expense, actor_user_id: int) -> None:
    if expense.is_recurring:
        return
    href = _expense_href(expense)
    actor_name = _actor_name(actor_user_id)
    desc = expense.description or "an expense"
    amount = expense.amount or 0
    message = f'{actor_name} added "{desc}" for {amount:.2f}'
    for user_id in _expense_recipient_ids(expense):
        if user_id == actor_user_id:
            continue
        create_notification(
            user_id,
            message,
            kind=NOTIFICATION_EXPENSE_ADDED,
            href=href,
            dedupe_key=f"expense_added:{expense.id}:{user_id}",
        )


def notify_expense_updated(expense: Expense, actor_user_id: int, *, event_key: str) -> None:
    href = _expense_href(expense)
    actor_name = _actor_name(actor_user_id)
    desc = expense.description or "an expense"
    message = f'{actor_name} updated "{desc}"'
    for user_id in _expense_recipient_ids(expense):
        if user_id == actor_user_id:
            continue
        create_notification(
            user_id,
            message,
            kind=NOTIFICATION_EXPENSE_UPDATED,
            href=href,
            dedupe_key=f"expense_updated:{expense.id}:{user_id}:{event_key}",
        )


def notify_settlement_requested(link: ExpensePaymentLink) -> None:
    expense = link.expense
    if expense is None or expense.paid_by is None:
        return
    if link.user_id == expense.paid_by:
        return
    owed = quantize_money(link.amount_owed or 0)
    if owed <= MONEY_EPSILON:
        return
    payer_name = _actor_name(expense.paid_by)
    desc = expense.description or "an expense"
    href = _expense_href(expense)
    create_notification(
        link.user_id,
        f'{payer_name} requested {owed:.2f} for "{desc}"',
        kind=NOTIFICATION_SETTLEMENT_REQUESTED,
        href=href,
        dedupe_key=f"settlement_requested:{link.id}",
    )


def notify_settlement_links_created(links: list[ExpensePaymentLink]) -> None:
    for link in links:
        notify_settlement_requested(link)


def notify_payment_received(link: ExpensePaymentLink) -> None:
    expense = link.expense
    guest = link.user
    if expense is None or expense.paid_by is None or guest is None:
        return
    if expense.paid_by == guest.id:
        return
    href = _expense_href(expense)
    desc = expense.description or "an expense"
    create_notification(
        expense.paid_by,
        f"{guest.name} paid {link.amount_owed:.2f} for “{desc}”",
        kind=NOTIFICATION_PAYMENT_RECEIVED,
        href=href,
        dedupe_key=f"payment_received:{link.id}",
    )
    create_notification(
        guest.id,
        f'Your payment of {link.amount_owed:.2f} for "{desc}" was recorded',
        kind=NOTIFICATION_SETTLEMENT_COMPLETED,
        href=href,
        dedupe_key=f"settlement_completed:{link.id}",
    )


def notify_reminder_sent(payer_user_id: int, guest_name: str, expense_description: str) -> None:
    desc = expense_description or "a shared expense"
    create_notification(
        payer_user_id,
        f'Reminder sent to {guest_name} for "{desc}"',
        kind=NOTIFICATION_REMINDER_SENT,
        href="/collect",
        dedupe_key=None,
    )


def notify_recurring_expense_generated(instance: Expense, template: Expense) -> None:
    if instance.recurrence_occurrence_date is None:
        return
    href = _expense_href(instance)
    desc = instance.description or "a recurring expense"
    amount = instance.amount or 0
    occurrence = instance.recurrence_occurrence_date.isoformat()
    payer_id = instance.paid_by
    for user_id in _expense_recipient_ids(instance):
        if user_id == payer_id:
            continue
        create_notification(
            user_id,
            f'Recurring expense "{desc}" ({amount:.2f}) was added',
            kind=NOTIFICATION_RECURRING_GENERATED,
            href=href,
            dedupe_key=f"recurring_generated:{template.id}:{occurrence}:{user_id}",
        )
