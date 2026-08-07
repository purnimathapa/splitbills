"""Who is on a standalone (non–split-group) expense."""

from __future__ import annotations

from models import Expense, ExpenseParticipant, TripMember, User, db

MAX_PARTICIPANTS = 50


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


def parse_payer_from_form(
    form,
    default_user_id: int,
    allowed_ids: set[int] | list[int],
) -> int:
    """Read ``paid_by_user_id``; must be among allowed participants."""
    allowed = set(allowed_ids)
    raw = (form.get("paid_by_user_id") or "").strip()
    if not raw:
        payer_id = default_user_id
    else:
        try:
            payer_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("Choose who paid for this expense.") from exc
    if payer_id not in allowed:
        raise ValueError("Payer must be one of the selected participants.")
    return payer_id


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


def validate_participants(
    payer_user_id: int,
    member_ids: list[int],
    *,
    trip_id: int | None = None,
    allowed_user_ids: set[int] | None = None,
) -> list[int]:
    """Ensure payer and split members are valid before creating an expense."""
    if payer_user_id not in member_ids:
        member_ids = sorted(set(member_ids) | {payer_user_id})

    if len(member_ids) < 2:
        raise ValueError("Pick at least one other person to split with.")
    if len(member_ids) > MAX_PARTICIPANTS:
        raise ValueError(f"An expense cannot have more than {MAX_PARTICIPANTS} people.")

    if trip_id is not None:
        trip_member_ids = set(get_trip_member_ids(trip_id))
        if payer_user_id not in trip_member_ids:
            raise ValueError("You must be a member of this group to add expenses.")
        invalid = set(member_ids) - trip_member_ids
        if invalid:
            raise ValueError("All participants must be members of this group.")
        return sorted(member_ids)

    if allowed_user_ids is not None:
        invalid = set(member_ids) - allowed_user_ids
        if invalid:
            raise ValueError("One or more participants are not on your friends list.")

    existing = {
        row[0]
        for row in db.session.query(User.id).filter(User.id.in_(member_ids)).all()
    }
    missing = set(member_ids) - existing
    if missing:
        raise ValueError("One or more participants could not be found.")

    return sorted(member_ids)


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


def user_can_modify_expense(user: User, expense: Expense) -> bool:
    """Only the payer may change expense data (finalize claims, future edit/delete)."""
    return expense.paid_by == user.id and user_can_access_expense(user, expense)


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
