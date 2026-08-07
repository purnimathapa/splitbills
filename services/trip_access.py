import random
import string

from sqlalchemy import or_

from flask import abort
from flask_login import current_user

from group_permissions import (
    PERM_VIEW_GROUP,
    ROLE_MEMBER,
    ROLE_OWNER,
    can,
)
from models import (
    Expense,
    ExpenseParticipant,
    Trip,
    TripMember,
    User,
    db,
)


def generate_invite_code():
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not Trip.query.filter_by(invite_code=code).first():
            return code


def get_membership(trip_id: int, user_id: int | None = None) -> TripMember | None:
    uid = user_id if user_id is not None else current_user.id
    return TripMember.query.filter_by(trip_id=trip_id, user_id=uid).first()


def require_trip_permission(trip_id: int, permission: str) -> tuple[Trip, TripMember]:
    """Load a group and membership; abort 403 when not allowed."""
    trip = db.session.get(Trip, trip_id)
    if trip is None:
        abort(404)
    membership = get_membership(trip_id)
    if membership is None or not can(membership.role, permission):
        abort(403)
    return trip, membership


def get_trip_or_redirect(trip_id: int) -> Trip:
    """Require any group member (view permission). Returns trip or aborts."""
    trip, _membership = require_trip_permission(trip_id, PERM_VIEW_GROUP)
    return trip


def get_user_trips():
    memberships = TripMember.query.filter_by(user_id=current_user.id).all()
    trip_ids = [membership.trip_id for membership in memberships]
    if not trip_ids:
        return []
    return Trip.query.filter(Trip.id.in_(trip_ids)).order_by(Trip.created_at.desc()).all()


def get_user_expenses():
    trips = get_user_trips()
    trip_ids = [trip.id for trip in trips]

    filters = [Expense.paid_by == current_user.id]
    if trip_ids:
        filters.append(Expense.trip_id.in_(trip_ids))

    participant_rows = (
        db.session.query(ExpenseParticipant.expense_id)
        .filter(ExpenseParticipant.user_id == current_user.id)
        .all()
    )
    participant_expense_ids = [row[0] for row in participant_rows]
    if participant_expense_ids:
        filters.append(Expense.id.in_(participant_expense_ids))

    expenses = (
        Expense.query.filter(or_(*filters))
        .order_by(Expense.created_at.desc())
        .all()
    )
    return trips, expenses


def get_trip_members(trip_id):
    memberships = TripMember.query.filter_by(trip_id=trip_id).all()
    member_ids = [membership.user_id for membership in memberships]
    if not member_ids:
        return []
    return User.query.filter(User.id.in_(member_ids)).order_by(User.name).all()


def membership_role_map(trip_id: int) -> dict[int, str]:
    rows = TripMember.query.filter_by(trip_id=trip_id).all()
    return {row.user_id: row.role for row in rows}
def ensure_trip_has_owner(trip: Trip) -> None:
    """Backfill owner when missing (e.g. legacy rows)."""
    owner = TripMember.query.filter_by(trip_id=trip.id, role=ROLE_OWNER).first()
    if owner:
        return
    if trip.created_by:
        creator = TripMember.query.filter_by(
            trip_id=trip.id,
            user_id=trip.created_by,
        ).first()
        if creator:
            creator.role = ROLE_OWNER
            return
    first = (
        TripMember.query.filter_by(trip_id=trip.id)
        .order_by(TripMember.id.asc())
        .first()
    )
    if first:
        first.role = ROLE_OWNER
