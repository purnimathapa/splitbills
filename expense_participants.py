"""Who is on a standalone (non–split-group) expense."""

from __future__ import annotations

from models import Expense, ExpenseParticipant, TripMember, User, db


def get_trip_member_ids(trip_id: int) -> list[int]:
    memberships = TripMember.query.filter_by(trip_id=trip_id).all()
    return [m.user_id for m in memberships]


def get_expense_member_ids(expense: Expense) -> list[int]:
    """Participants for splits, claims, and payment links."""
    if expense.trip_id:
        return get_trip_member_ids(expense.trip_id)
    rows = ExpenseParticipant.query.filter_by(expense_id=expense.id).all()
    return [r.user_id for r in rows]


def get_expense_members(expense: Expense) -> list[User]:
    ids = get_expense_member_ids(expense)
    if not ids:
        return []
    return User.query.filter(User.id.in_(ids)).order_by(User.name).all()


def persist_expense_participants(expense_id: int, user_ids: list[int]) -> None:
    seen: set[int] = set()
    for user_id in user_ids:
        if user_id in seen:
            continue
        seen.add(user_id)
        db.session.add(
            ExpenseParticipant(expense_id=expense_id, user_id=user_id)
        )


def parse_participant_ids_from_form(form, payer_user_id: int) -> list[int]:
    """Read `participant_user_ids` checkboxes; payer is always included."""
    raw = form.getlist("participant_user_ids")
    ids: set[int] = {int(payer_user_id)}
    for value in raw:
        try:
            uid = int(value)
        except (TypeError, ValueError):
            continue
        ids.add(uid)
    return sorted(ids)


def user_can_access_expense(user: User, expense: Expense) -> bool:
    if expense.paid_by == user.id:
        return True
    if expense.trip_id:
        return (
            TripMember.query.filter_by(
                trip_id=expense.trip_id,
                user_id=user.id,
            ).first()
            is not None
        )
    return (
        ExpenseParticipant.query.filter_by(
            expense_id=expense.id,
            user_id=user.id,
        ).first()
        is not None
    )


def visible_expense_ids_for_user(user_id: int, trip_ids: list[int]) -> list[int]:
    """Expenses the user may see (group, participant, or payer)."""
    ids: set[int] = set()
    if trip_ids:
        for row in (
            db.session.query(Expense.id)
            .filter(Expense.trip_id.in_(trip_ids))
            .all()
        ):
            ids.add(row[0])
    for row in (
        db.session.query(Expense.id).filter(Expense.paid_by == user_id).all()
    ):
        ids.add(row[0])
    for row in (
        db.session.query(ExpenseParticipant.expense_id)
        .filter(ExpenseParticipant.user_id == user_id)
        .all()
    ):
        ids.add(row[0])
    return list(ids)
