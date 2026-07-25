"""Daily job: materialize due recurring expenses from templates."""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta

from models import (
    RECURRENCE_INTERVALS,
    RECURRENCE_MONTHLY,
    RECURRENCE_WEEKLY,
    SPLIT_TYPE_ITEMIZED,
    Expense,
    ExpenseItem,
    ExpenseItemAssignment,
    ExpensePaymentLink,
    ExpenseSplit,
    db,
)
from payment_links import create_expense_payment_links

logger = logging.getLogger(__name__)


def add_months(d: date, months: int = 1) -> date:
    """Advance a calendar date by N months, clamping the day to month length."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def advance_recurrence(from_day: date, interval: str) -> date:
    if interval == RECURRENCE_WEEKLY:
        return from_day + timedelta(days=7)
    if interval == RECURRENCE_MONTHLY:
        return add_months(from_day, 1)
    raise ValueError(f"Unknown recurrence interval: {interval}")


def initial_next_occurrence(from_day: date, interval: str) -> date:
    """First auto-generated occurrence after the template expense is created."""
    return advance_recurrence(from_day, interval)


def splits_owed_by_user(expense: Expense) -> dict[int, float]:
    return {
        split.user_id: float(split.amount_owed or 0)
        for split in expense.splits
        if (split.amount_owed or 0) > 0
    }


def clone_expense_from_template(template: Expense) -> Expense:
    """Create a one-off expense instance copied from a recurring template."""
    instance = Expense(
        trip_id=template.trip_id,
        paid_by=template.paid_by,
        category=template.category,
        description=template.description,
        amount=template.amount,
        remarks=template.remarks,
        split_type=template.split_type,
        tax_tip_amount=template.tax_tip_amount,
        receipt_image_url=None,
        is_recurring=False,
        recurrence_interval=None,
        next_occurrence_date=None,
        created_at=datetime.utcnow(),
    )
    db.session.add(instance)
    db.session.flush()

    for split in template.splits:
        db.session.add(
            ExpenseSplit(
                expense_id=instance.id,
                user_id=split.user_id,
                amount_owed=split.amount_owed,
                percentage=split.percentage,
                shares=split.shares,
            )
        )

    if template.split_type == SPLIT_TYPE_ITEMIZED:
        for src_item in template.items:
            item = ExpenseItem(
                expense_id=instance.id,
                name=src_item.name,
                price=src_item.price,
                quantity=src_item.quantity,
            )
            db.session.add(item)
            db.session.flush()
            for assignment in src_item.assignments:
                db.session.add(
                    ExpenseItemAssignment(
                        expense_item_id=item.id,
                        user_id=assignment.user_id,
                    )
                )

    db.session.flush()
    return instance


def find_due_recurring_templates(as_of: date) -> list[Expense]:
    return (
        Expense.query.filter(
            Expense.is_recurring.is_(True),
            Expense.next_occurrence_date.isnot(None),
            Expense.next_occurrence_date <= as_of,
        )
        .order_by(Expense.id)
        .all()
    )


def process_recurring_template(template: Expense, as_of: date) -> int:
    """Create instances for each missed occurrence up to as_of. Returns count created."""
    if not template.is_recurring or not template.next_occurrence_date:
        return 0
    if template.recurrence_interval not in RECURRENCE_INTERVALS:
        logger.warning(
            "Skipping recurring expense %s: invalid interval %r",
            template.id,
            template.recurrence_interval,
        )
        return 0

    created = 0
    while template.next_occurrence_date and template.next_occurrence_date <= as_of:
        instance = clone_expense_from_template(template)
        owed = splits_owed_by_user(instance)
        create_expense_payment_links(
            instance,
            owed,
            db.session,
            ExpensePaymentLink,
        )
        template.next_occurrence_date = advance_recurrence(
            template.next_occurrence_date,
            template.recurrence_interval,
        )
        created += 1
    return created


def run_recurring_expense_job(app) -> dict:
    """Entry point for the daily scheduler."""
    stats = {"due_templates": 0, "created": 0, "errors": 0}
    as_of = date.today()

    with app.app_context():
        templates = find_due_recurring_templates(as_of)
        stats["due_templates"] = len(templates)

        for template in templates:
            try:
                count = process_recurring_template(template, as_of)
                db.session.commit()
                stats["created"] += count
                if count:
                    logger.info(
                        "Recurring expense template_id=%s created %s instance(s)",
                        template.id,
                        count,
                    )
            except Exception:
                db.session.rollback()
                stats["errors"] += 1
                logger.exception(
                    "Failed recurring expense processing for template_id=%s",
                    template.id,
                )

    return stats


def parse_recurrence_from_form(form) -> tuple[bool, str | None, date | None]:
    """Read recurring fields from an add-expense POST."""
    is_recurring = form.get("is_recurring") == "on"
    if not is_recurring:
        return False, None, None

    interval = (form.get("recurrence_interval") or RECURRENCE_MONTHLY).strip().lower()
    if interval not in RECURRENCE_INTERVALS:
        raise ValueError("Choose weekly or monthly recurrence.")

    next_date = initial_next_occurrence(date.today(), interval)
    return True, interval, next_date
