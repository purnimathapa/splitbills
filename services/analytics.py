"""User analytics — SQL aggregations for spending and settlement metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, case, func, literal, or_

from models import (
    PAYMENT_STATUS_PAID,
    PAYMENT_STATUS_PENDING,
    RECURRENCE_MONTHLY,
    RECURRENCE_WEEKLY,
    Expense,
    ExpenseParticipant,
    ExpensePaymentLink,
    Trip,
    TripMember,
    db,
)
from money import ZERO, quantize_money

ANALYTICS_RANGES = {
    "30": 30,
    "90": 90,
    "all": None,
}


def parse_range_key(raw: str | None) -> str:
    key = (raw or "30").strip().lower()
    return key if key in ANALYTICS_RANGES else "30"


def range_cutoff(range_key: str) -> datetime | None:
    days = ANALYTICS_RANGES[range_key]
    if days is None:
        return None
    return datetime.utcnow() - timedelta(days=days)


def _chart_values(totals: dict[str, Decimal], labels: list[str]) -> list[float]:
    return [float(quantize_money(totals[label])) for label in labels]


def _user_trip_ids(user_id: int, *, active_only: bool) -> list[int]:
    query = (
        db.session.query(TripMember.trip_id)
        .join(Trip, Trip.id == TripMember.trip_id)
        .filter(TripMember.user_id == user_id)
    )
    if active_only:
        query = query.filter(Trip.is_active.is_(True))
    return [row[0] for row in query.all()]


def _participant_expense_ids(user_id: int) -> list[int]:
    rows = (
        db.session.query(ExpenseParticipant.expense_id)
        .filter(ExpenseParticipant.user_id == user_id)
        .all()
    )
    return [row[0] for row in rows]


def visible_expense_filter(user_id: int, *, active_trips_only: bool = True):
    """Expenses the user paid, belongs to via group, or joined as participant."""
    trip_ids = _user_trip_ids(user_id, active_only=active_trips_only)
    participant_ids = _participant_expense_ids(user_id)

    clauses = []
    if trip_ids:
        clauses.append(Expense.trip_id.in_(trip_ids))
    clauses.append(and_(Expense.trip_id.is_(None), Expense.paid_by == user_id))
    if participant_ids:
        clauses.append(
            and_(Expense.trip_id.is_(None), Expense.id.in_(participant_ids))
        )
    if not clauses:
        return Expense.id < 0
    return or_(*clauses)


def _spending_expense_filter(user_id: int, range_key: str):
    """Non-recurring expenses in range; group spend limited to active groups."""
    conditions = [
        visible_expense_filter(user_id, active_trips_only=True),
        Expense.is_recurring.is_(False),
    ]
    cutoff = range_cutoff(range_key)
    if cutoff is not None:
        conditions.append(Expense.created_at >= cutoff)
    return and_(*conditions)


def _category_label_expr():
    trimmed = func.trim(Expense.category)
    return case(
        (or_(Expense.category.is_(None), trimmed == ""), literal("General")),
        else_=trimmed,
    )


@dataclass
class SettlementMetrics:
    amount_owed: Decimal = ZERO
    amount_receivable: Decimal = ZERO
    pending_count: int = 0
    paid_count: int = 0
    pending_total: Decimal = ZERO
    collected_total: Decimal = ZERO


@dataclass
class RecurringMetrics:
    active_count: int = 0
    weekly_count: int = 0
    monthly_count: int = 0
    per_occurrence_total: Decimal = ZERO


@dataclass
class UserAnalytics:
    range_key: str
    total_spending: Decimal = ZERO
    expense_count: int = 0
    group_spending: Decimal = ZERO
    personal_spending: Decimal = ZERO
    category_labels: list[str] = field(default_factory=list)
    category_values: list[float] = field(default_factory=list)
    trip_labels: list[str] = field(default_factory=list)
    trip_values: list[float] = field(default_factory=list)
    trend_labels: list[str] = field(default_factory=list)
    trend_values: list[float] = field(default_factory=list)
    settlement: SettlementMetrics = field(default_factory=SettlementMetrics)
    recurring: RecurringMetrics = field(default_factory=RecurringMetrics)
    trip_count: int = 0
    friend_count: int = 0

    @property
    def has_spending_data(self) -> bool:
        return self.expense_count > 0


def aggregate_total_spending(user_id: int, range_key: str) -> tuple[Decimal, int]:
    row = (
        db.session.query(
            func.coalesce(func.sum(Expense.amount), 0),
            func.count(Expense.id),
        )
        .filter(_spending_expense_filter(user_id, range_key))
        .one()
    )
    return quantize_money(row[0]), int(row[1] or 0)


def aggregate_group_vs_personal(user_id: int, range_key: str) -> tuple[Decimal, Decimal]:
    base = _spending_expense_filter(user_id, range_key)
    group_total = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0))
        .filter(base, Expense.trip_id.isnot(None))
        .scalar()
    )
    personal_total = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0))
        .filter(base, Expense.trip_id.is_(None))
        .scalar()
    )
    return quantize_money(group_total), quantize_money(personal_total)


def aggregate_category_spending(user_id: int, range_key: str) -> tuple[list[str], list[float]]:
    category = _category_label_expr()
    rows = (
        db.session.query(
            category.label("label"),
            func.coalesce(func.sum(Expense.amount), 0).label("total"),
        )
        .filter(_spending_expense_filter(user_id, range_key))
        .group_by(category)
        .order_by(func.sum(Expense.amount).desc())
        .all()
    )
    if not rows:
        return [], []
    totals = {row.label: quantize_money(row.total) for row in rows}
    labels = list(totals.keys())
    return labels, _chart_values(totals, labels)


def aggregate_trip_spending(user_id: int, range_key: str) -> tuple[list[str], list[float]]:
    rows = (
        db.session.query(
            Trip.trip_name.label("label"),
            func.coalesce(func.sum(Expense.amount), 0).label("total"),
        )
        .join(Expense, Expense.trip_id == Trip.id)
        .filter(
            _spending_expense_filter(user_id, range_key),
            Expense.trip_id.isnot(None),
            Trip.is_active.is_(True),
        )
        .group_by(Trip.id, Trip.trip_name)
        .order_by(func.sum(Expense.amount).desc())
        .all()
    )
    if not rows:
        return [], []
    totals = {row.label: quantize_money(row.total) for row in rows}
    labels = list(totals.keys())
    return labels, _chart_values(totals, labels)


def aggregate_spending_trend(user_id: int, range_key: str) -> tuple[list[str], list[float]]:
    day_bucket = func.date(Expense.created_at)
    rows = (
        db.session.query(
            day_bucket.label("day"),
            func.coalesce(func.sum(Expense.amount), 0).label("total"),
        )
        .filter(
            _spending_expense_filter(user_id, range_key),
            Expense.created_at.isnot(None),
        )
        .group_by(day_bucket)
        .order_by(day_bucket.asc())
        .all()
    )
    if not rows:
        return [], []
    totals = {str(row.day): quantize_money(row.total) for row in rows if row.day}
    labels = sorted(totals.keys())
    return labels, _chart_values(totals, labels)


def aggregate_settlement_metrics(user_id: int) -> SettlementMetrics:
    """Owed/receivable from payment links on all visible expenses (incl. archived groups)."""
    visible = visible_expense_filter(user_id, active_trips_only=False)

    amount_owed = (
        db.session.query(func.coalesce(func.sum(ExpensePaymentLink.amount_owed), 0))
        .join(Expense, ExpensePaymentLink.expense_id == Expense.id)
        .filter(
            visible,
            ExpensePaymentLink.user_id == user_id,
            ExpensePaymentLink.status == PAYMENT_STATUS_PENDING,
            Expense.is_recurring.is_(False),
        )
        .scalar()
    )

    amount_receivable = (
        db.session.query(func.coalesce(func.sum(ExpensePaymentLink.amount_owed), 0))
        .join(Expense, ExpensePaymentLink.expense_id == Expense.id)
        .filter(
            visible,
            Expense.paid_by == user_id,
            ExpensePaymentLink.user_id != user_id,
            ExpensePaymentLink.status == PAYMENT_STATUS_PENDING,
            Expense.is_recurring.is_(False),
        )
        .scalar()
    )

    status_rows = (
        db.session.query(
            ExpensePaymentLink.status,
            func.count(ExpensePaymentLink.id),
            func.coalesce(func.sum(ExpensePaymentLink.amount_owed), 0),
        )
        .join(Expense, ExpensePaymentLink.expense_id == Expense.id)
        .filter(
            visible,
            or_(
                Expense.paid_by == user_id,
                ExpensePaymentLink.user_id == user_id,
            ),
            Expense.is_recurring.is_(False),
        )
        .group_by(ExpensePaymentLink.status)
        .all()
    )

    pending_count = 0
    paid_count = 0
    pending_total = ZERO
    collected_total = ZERO
    for status, count, total in status_rows:
        amount = quantize_money(total)
        if status == PAYMENT_STATUS_PENDING:
            pending_count += int(count or 0)
            pending_total += amount
        elif status == PAYMENT_STATUS_PAID:
            paid_count += int(count or 0)
            collected_total += amount

    return SettlementMetrics(
        amount_owed=quantize_money(amount_owed),
        amount_receivable=quantize_money(amount_receivable),
        pending_count=pending_count,
        paid_count=paid_count,
        pending_total=quantize_money(pending_total),
        collected_total=quantize_money(collected_total),
    )


def aggregate_recurring_metrics(user_id: int) -> RecurringMetrics:
    trip_ids = _user_trip_ids(user_id, active_only=True)
    clauses = [Expense.paid_by == user_id]
    if trip_ids:
        clauses.append(Expense.trip_id.in_(trip_ids))

    row = (
        db.session.query(
            func.count(Expense.id),
            func.coalesce(func.sum(Expense.amount), 0),
            func.coalesce(
                func.sum(case((Expense.recurrence_interval == RECURRENCE_WEEKLY, 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(case((Expense.recurrence_interval == RECURRENCE_MONTHLY, 1), else_=0)),
                0,
            ),
        )
        .filter(
            Expense.is_recurring.is_(True),
            Expense.next_occurrence_date.isnot(None),
            or_(*clauses),
        )
        .one()
    )
    active_count = int(row[0] or 0)
    if active_count == 0:
        return RecurringMetrics()

    return RecurringMetrics(
        active_count=active_count,
        weekly_count=int(row[2] or 0),
        monthly_count=int(row[3] or 0),
        per_occurrence_total=quantize_money(row[1]),
    )


def build_user_analytics(
    user_id: int,
    range_key: str,
    *,
    trip_count: int = 0,
    friend_count: int = 0,
) -> UserAnalytics:
    range_key = parse_range_key(range_key)
    total, count = aggregate_total_spending(user_id, range_key)
    group_total, personal_total = aggregate_group_vs_personal(user_id, range_key)
    category_labels, category_values = aggregate_category_spending(user_id, range_key)
    trip_labels, trip_values = aggregate_trip_spending(user_id, range_key)
    trend_labels, trend_values = aggregate_spending_trend(user_id, range_key)

    return UserAnalytics(
        range_key=range_key,
        total_spending=total,
        expense_count=count,
        group_spending=group_total,
        personal_spending=personal_total,
        category_labels=category_labels,
        category_values=category_values,
        trip_labels=trip_labels,
        trip_values=trip_values,
        trend_labels=trend_labels,
        trend_values=trend_values,
        settlement=aggregate_settlement_metrics(user_id),
        recurring=aggregate_recurring_metrics(user_id),
        trip_count=trip_count,
        friend_count=friend_count,
    )
