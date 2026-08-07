"""Daily job: materialize due recurring expenses from templates."""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta, timezone

from decimal import Decimal

from sqlalchemy.exc import IntegrityError

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
from money import quantize_money
from notifications import (
    notify_recurring_expense_generated,
    notify_settlement_links_created,
)

logger = logging.getLogger(__name__)

# Cap catch-up so a long-idle template cannot loop forever in one run.
MAX_CATCHUP_PER_RUN = 52


def utc_today() -> date:
    """Calendar date in UTC (matches scheduler cron timezone)."""
    return datetime.now(timezone.utc).date()


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


def is_template_active(template: Expense, as_of: date) -> bool:
    """Recurring templates are active when flagged and not past their end date."""
    if not template.is_recurring or not template.next_occurrence_date:
        return False
    if template.recurrence_end_date and template.next_occurrence_date > template.recurrence_end_date:
        return False
    return True


def deactivate_template(template: Expense) -> None:
    """Stop future generation for a template."""
    template.is_recurring = False
    template.next_occurrence_date = None


def occurrence_within_end(template: Expense, occurrence: date) -> bool:
    end = template.recurrence_end_date
    return end is None or occurrence <= end


def splits_owed_by_user(expense: Expense) -> dict[int, Decimal]:
    return {
        split.user_id: quantize_money(split.amount_owed or 0)
        for split in expense.splits
        if quantize_money(split.amount_owed or 0) > 0
    }


def instance_exists_for_occurrence(template_id: int, occurrence: date) -> bool:
    return (
        Expense.query.filter_by(
            recurring_template_id=template_id,
            recurrence_occurrence_date=occurrence,
        ).first()
        is not None
    )


def clone_expense_from_template(
    template: Expense,
    occurrence: date,
) -> Expense:
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
        recurrence_end_date=None,
        recurring_template_id=template.id,
        recurrence_occurrence_date=occurrence,
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
    if not is_template_active(template, as_of):
        if template.is_recurring and template.recurrence_end_date:
            deactivate_template(template)
        return 0
    if template.recurrence_interval not in RECURRENCE_INTERVALS:
        logger.warning(
            "Skipping recurring expense %s: invalid interval %r",
            template.id,
            template.recurrence_interval,
        )
        return 0

    created = 0
    iterations = 0
    while (
        template.next_occurrence_date
        and template.next_occurrence_date <= as_of
        and iterations < MAX_CATCHUP_PER_RUN
    ):
        iterations += 1
        occurrence = template.next_occurrence_date

        if not occurrence_within_end(template, occurrence):
            deactivate_template(template)
            break

        if instance_exists_for_occurrence(template.id, occurrence):
            logger.info(
                "Recurring template_id=%s occurrence %s already exists; advancing",
                template.id,
                occurrence,
            )
            template.next_occurrence_date = advance_recurrence(
                occurrence,
                template.recurrence_interval,
            )
            continue

        try:
            with db.session.begin_nested():
                instance = clone_expense_from_template(template, occurrence)
                owed = splits_owed_by_user(instance)
                created_links = create_expense_payment_links(
                    instance,
                    owed,
                    db.session,
                    ExpensePaymentLink,
                )
                notify_recurring_expense_generated(instance, template)
                notify_settlement_links_created(created_links)
        except IntegrityError:
            logger.info(
                "Duplicate recurring instance for template_id=%s occurrence %s",
                template.id,
                occurrence,
            )
            template.next_occurrence_date = advance_recurrence(
                occurrence,
                template.recurrence_interval,
            )
            continue

        template.next_occurrence_date = advance_recurrence(
            occurrence,
            template.recurrence_interval,
        )
        created += 1

        if (
            template.next_occurrence_date
            and template.recurrence_end_date
            and template.next_occurrence_date > template.recurrence_end_date
        ):
            deactivate_template(template)
            break

    if iterations >= MAX_CATCHUP_PER_RUN:
        logger.warning(
            "Recurring template_id=%s hit catch-up cap (%s); remaining dates deferred",
            template.id,
            MAX_CATCHUP_PER_RUN,
        )

    return created


def run_recurring_expense_job(app, as_of: date | None = None) -> dict:
    """Entry point for the daily scheduler."""
    stats = {"due_templates": 0, "created": 0, "errors": 0}
    if as_of is None:
        as_of = utc_today()

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


def parse_recurrence_end_date(raw: str | None) -> date | None:
    if not raw or not str(raw).strip():
        return None
    return datetime.strptime(str(raw).strip(), "%Y-%m-%d").date()


def parse_recurrence_from_form(form) -> tuple[bool, str | None, date | None, date | None]:
    """Read recurring fields from an add-expense POST."""
    is_recurring = form.get("is_recurring") == "on"
    if not is_recurring:
        return False, None, None, None

    interval = (form.get("recurrence_interval") or RECURRENCE_MONTHLY).strip().lower()
    if interval not in RECURRENCE_INTERVALS:
        raise ValueError("Choose weekly or monthly recurrence.")

    start_day = utc_today()
    next_date = initial_next_occurrence(start_day, interval)
    end_date = parse_recurrence_end_date(form.get("recurrence_end_date"))
    if end_date and end_date < start_day:
        raise ValueError("Recurrence end date cannot be before today.")
    if end_date and next_date > end_date:
        raise ValueError("First recurrence would fall after the end date.")

    return True, interval, next_date, end_date
