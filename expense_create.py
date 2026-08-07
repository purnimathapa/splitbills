"""Shared expense creation from add-expense form payload."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from expense_participants import (
    parse_payer_from_form,
    persist_expense_participants,
    validate_participants,
)
from expense_split_logic import (
    compute_equal_split,
    compute_exact_split,
    compute_itemized_split,
    compute_percentage_split,
    compute_shares_split,
    parse_itemized_form,
    parse_member_amount_fields,
)
from item_claims import (
    create_self_service_payment_links,
    parse_itemized_line_items_only,
)
from models import (
    SPLIT_TYPE_EQUAL,
    SPLIT_TYPE_EXACT,
    SPLIT_TYPE_ITEMIZED,
    SPLIT_TYPE_PERCENTAGE,
    SPLIT_TYPE_SHARES,
    Expense,
    ExpenseItem,
    ExpenseItemAssignment,
    ExpenseParticipant,
    ExpensePaymentLink,
    ExpenseSplit,
    db,
)
from money import ZERO, assert_split_covers_total, quantize_money, to_decimal
from payment_links import create_expense_payment_links
from recurring_expenses import parse_recurrence_from_form
from notifications import notify_expense_added, notify_expense_updated, notify_settlement_links_created

if TYPE_CHECKING:
    from flask import Flask

MAX_DESCRIPTION_LEN = 255
MAX_CATEGORY_LEN = 100
MAX_REMARKS_LEN = 255
MAX_EXPENSE_AMOUNT = Decimal("999999999.99")
MIN_EXPENSE_AMOUNT = Decimal("0.01")
MAX_FUTURE_EXPENSE_DAYS = 1

EXPENSE_CATEGORIES = (
    "General",
    "Food & drink",
    "Transport",
    "Rent",
    "Utilities",
    "Entertainment",
    "Shopping",
    "Travel",
    "Health",
    "Other",
)


def validate_description(raw: str) -> str:
    description = (raw or "").strip()
    if not description:
        raise ValueError("Enter a description for this expense.")
    if len(description) > MAX_DESCRIPTION_LEN:
        raise ValueError(
            f"Description must be at most {MAX_DESCRIPTION_LEN} characters."
        )
    return description


def validate_category(raw: str) -> str:
    category = (raw or "General").strip() or "General"
    if len(category) > MAX_CATEGORY_LEN:
        raise ValueError(f"Category must be at most {MAX_CATEGORY_LEN} characters.")
    return category


def validate_remarks(raw: str) -> str:
    remarks = (raw or "").strip()
    if len(remarks) > MAX_REMARKS_LEN:
        raise ValueError(f"Remarks must be at most {MAX_REMARKS_LEN} characters.")
    return remarks


def parse_expense_amount(raw: str) -> Decimal:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Enter a valid amount.")
    try:
        amount = to_decimal(text)
    except ValueError as exc:
        raise ValueError("Enter a valid amount.") from exc
    if amount < MIN_EXPENSE_AMOUNT:
        raise ValueError("Amount must be greater than zero.")
    if amount > MAX_EXPENSE_AMOUNT:
        raise ValueError("Amount is too large.")
    return amount


def parse_expense_date(form) -> datetime | None:
    """Optional expense_date (YYYY-MM-DD); defaults to DB created_at when omitted."""
    raw = form.get("expense_date", "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Expense date must be YYYY-MM-DD.") from exc
    today = date.today()
    if parsed.date() > today + timedelta(days=MAX_FUTURE_EXPENSE_DAYS):
        raise ValueError("Expense date cannot be in the future.")
    if parsed.year < 2000:
        raise ValueError("Expense date must be year 2000 or later.")
    return parsed


def _parse_simple_amount(form) -> Decimal:
    amount_raw = (
        form.get("amount", "")
        or form.get("amount_exact", "")
        or form.get("amount_pct", "")
        or form.get("amount_shares", "")
        or ""
    )
    return parse_expense_amount(amount_raw)


def _resolve_split_method(form) -> str:
    if form.get("use_itemized") == "on" or form.get("split_method") == "itemized":
        return "itemized"
    method = (form.get("split_method") or form.get("split_method_advanced") or "equal").strip()
    if method not in ("equal", "exact", "percentage", "shares", "itemized"):
        raise ValueError("Invalid split method.")
    return method


def _use_itemized_form(form) -> bool:
    return form.get("use_itemized") == "on" or _resolve_split_method(form) == "itemized"


def _persist_expense_splits(expense_id: int, owed_by_user: dict[int, Decimal]) -> None:
    for user_id, amount_owed in owed_by_user.items():
        if amount_owed <= ZERO:
            continue
        db.session.add(
            ExpenseSplit(
                expense_id=expense_id,
                user_id=user_id,
                amount_owed=quantize_money(amount_owed),
            )
        )


def create_expense_from_form(
    form,
    receipt_file,
    *,
    payer_user_id: int,
    member_ids: list[int],
    trip_id: int | None,
    app: Flask,
    save_receipt_fn,
    log_created_fn,
    allowed_user_ids: set[int] | None = None,
) -> Expense:
    """Create expense, splits, items, payment links, optional receipt."""
    member_ids = validate_participants(
        payer_user_id,
        member_ids,
        trip_id=trip_id,
        allowed_user_ids=allowed_user_ids,
    )
    payer_user_id = parse_payer_from_form(form, payer_user_id, member_ids)

    description = validate_description(form.get("description", ""))
    remarks = validate_remarks(form.get("remarks", ""))
    category = validate_category(form.get("category", "General"))
    expense_date = parse_expense_date(form)
    use_itemized = _use_itemized_form(form)
    self_service = form.get("self_service_items") == "on" and use_itemized
    member_id_set = set(member_ids)

    owed_by_user: dict[int, Decimal] = {}

    if use_itemized:
        if self_service:
            line_items, tax_tip_amount = parse_itemized_line_items_only(
                form, member_id_set
            )
            items_subtotal = quantize_money(
                sum((i.price * i.quantity for i in line_items), ZERO)
            )
            owed_by_user = {}
        else:
            line_items, tax_tip_amount = parse_itemized_form(form, member_id_set)
            owed_by_user, items_subtotal = compute_itemized_split(
                line_items,
                tax_tip_amount,
                member_ids,
            )
        amount = quantize_money(items_subtotal + tax_tip_amount)

        if amount <= ZERO:
            raise ValueError("Add line items so the total is greater than zero.")

        expense = Expense(
            trip_id=trip_id,
            paid_by=payer_user_id,
            category=category,
            description=description,
            amount=amount,
            remarks=remarks,
            split_type=SPLIT_TYPE_ITEMIZED,
            tax_tip_amount=quantize_money(tax_tip_amount),
            self_service_items=self_service,
        )
        if expense_date:
            expense.created_at = expense_date
        db.session.add(expense)
        db.session.flush()

        if trip_id is None:
            persist_expense_participants(expense.id, member_ids)

        for parsed_item in line_items:
            item = ExpenseItem(
                expense_id=expense.id,
                name=parsed_item.name,
                price=quantize_money(parsed_item.price),
                quantity=parsed_item.quantity,
            )
            db.session.add(item)
            db.session.flush()
            if not self_service:
                for user_id in parsed_item.assigned_user_ids:
                    if user_id in member_ids:
                        db.session.add(
                            ExpenseItemAssignment(
                                expense_item_id=item.id,
                                user_id=user_id,
                            )
                        )

        if not self_service:
            assert_split_covers_total(amount, owed_by_user)
            _persist_expense_splits(expense.id, owed_by_user)
    else:
        split_method = _resolve_split_method(form)
        if split_method == "itemized":
            raise ValueError("Turn on itemized split or choose another method.")
        amount = _parse_simple_amount(form)

        split_type = SPLIT_TYPE_EQUAL
        if split_method == "exact":
            split_type = SPLIT_TYPE_EXACT
            owed_by_user = compute_exact_split(
                amount,
                parse_member_amount_fields(form, member_ids, "split_exact"),
            )
        elif split_method == "percentage":
            split_type = SPLIT_TYPE_PERCENTAGE
            owed_by_user = compute_percentage_split(
                amount,
                parse_member_amount_fields(form, member_ids, "split_pct"),
            )
        elif split_method == "shares":
            split_type = SPLIT_TYPE_SHARES
            owed_by_user = compute_shares_split(
                amount,
                parse_member_amount_fields(form, member_ids, "split_shares"),
            )
        else:
            owed_by_user = compute_equal_split(amount, member_ids)

        assert_split_covers_total(amount, owed_by_user)

        expense = Expense(
            trip_id=trip_id,
            paid_by=payer_user_id,
            category=category,
            description=description,
            amount=quantize_money(amount),
            remarks=remarks,
            split_type=split_type,
            tax_tip_amount=ZERO,
        )
        if expense_date:
            expense.created_at = expense_date
        db.session.add(expense)
        db.session.flush()

        if trip_id is None:
            persist_expense_participants(expense.id, member_ids)

        _persist_expense_splits(expense.id, owed_by_user)

    if getattr(expense, "self_service_items", False):
        links = create_self_service_payment_links(
            expense,
            member_ids,
            db.session,
            ExpensePaymentLink,
        )
    else:
        links = create_expense_payment_links(
            expense,
            owed_by_user,
            db.session,
            ExpensePaymentLink,
        )

    is_rec, interval, next_dt, end_dt = parse_recurrence_from_form(form)
    expense.is_recurring = is_rec
    expense.recurrence_interval = interval
    expense.next_occurrence_date = next_dt
    expense.recurrence_end_date = end_dt

    if receipt_file and receipt_file.filename:
        expense.receipt_image_url = save_receipt_fn(app, receipt_file)

    log_created_fn(expense, payer_user_id)
    notify_expense_added(expense, payer_user_id)
    if not is_rec and not getattr(expense, "self_service_items", False):
        notify_settlement_links_created(links)
    return expense


def _clear_expense_children(expense: Expense) -> None:
    ExpensePaymentLink.query.filter_by(expense_id=expense.id).delete()
    ExpenseSplit.query.filter_by(expense_id=expense.id).delete()
    for item in list(expense.items):
        ExpenseItemAssignment.query.filter(
            ExpenseItemAssignment.expense_item_id == item.id
        ).delete()
    ExpenseItem.query.filter_by(expense_id=expense.id).delete()
    if expense.trip_id is None:
        ExpenseParticipant.query.filter_by(expense_id=expense.id).delete()


def update_expense_from_form(
    expense: Expense,
    form,
    receipt_file,
    *,
    payer_user_id: int,
    member_ids: list[int],
    trip_id: int | None,
    app: Flask,
    save_receipt_fn,
    allowed_user_ids: set[int] | None = None,
) -> Expense:
    """Replace splits/items on an existing expense (payer-only)."""
    if getattr(expense, "self_service_items", False):
        raise ValueError(
            "Self-service receipt splits can't be edited here. Create a new split instead."
        )
    if expense.recurring_template_id:
        raise ValueError(
            "Generated recurring copies can't be edited. Edit the recurring template instead."
        )

    member_ids = validate_participants(
        payer_user_id,
        member_ids,
        trip_id=trip_id,
        allowed_user_ids=allowed_user_ids,
    )
    payer_user_id = parse_payer_from_form(form, payer_user_id, member_ids)

    description = validate_description(form.get("description", ""))
    remarks = validate_remarks(form.get("remarks", ""))
    category = validate_category(form.get("category", "General"))
    expense_date = parse_expense_date(form)
    use_itemized = _use_itemized_form(form)
    self_service = form.get("self_service_items") == "on" and use_itemized
    if self_service:
        raise ValueError("Self-service itemized splits can't be edited.")

    member_id_set = set(member_ids)
    owed_by_user: dict[int, Decimal] = {}
    _clear_expense_children(expense)

    if use_itemized:
        line_items, tax_tip_amount = parse_itemized_form(form, member_id_set)
        owed_by_user, items_subtotal = compute_itemized_split(
            line_items,
            tax_tip_amount,
            member_ids,
        )
        amount = quantize_money(items_subtotal + tax_tip_amount)
        if amount <= ZERO:
            raise ValueError("Add line items so the total is greater than zero.")

        expense.paid_by = payer_user_id
        expense.category = category
        expense.description = description
        expense.amount = amount
        expense.remarks = remarks
        expense.split_type = SPLIT_TYPE_ITEMIZED
        expense.tax_tip_amount = quantize_money(tax_tip_amount)
        expense.self_service_items = False
        if expense_date:
            expense.created_at = expense_date

        if trip_id is None:
            persist_expense_participants(expense.id, member_ids)

        for parsed_item in line_items:
            item = ExpenseItem(
                expense_id=expense.id,
                name=parsed_item.name,
                price=quantize_money(parsed_item.price),
                quantity=parsed_item.quantity,
            )
            db.session.add(item)
            db.session.flush()
            for user_id in parsed_item.assigned_user_ids:
                if user_id in member_ids:
                    db.session.add(
                        ExpenseItemAssignment(
                            expense_item_id=item.id,
                            user_id=user_id,
                        )
                    )

        assert_split_covers_total(amount, owed_by_user)
        _persist_expense_splits(expense.id, owed_by_user)
    else:
        split_method = _resolve_split_method(form)
        if split_method == "itemized":
            raise ValueError("Choose a split method or switch to itemized.")
        amount = _parse_simple_amount(form)

        split_type = SPLIT_TYPE_EQUAL
        if split_method == "exact":
            split_type = SPLIT_TYPE_EXACT
            owed_by_user = compute_exact_split(
                amount,
                parse_member_amount_fields(form, member_ids, "split_exact"),
            )
        elif split_method == "percentage":
            split_type = SPLIT_TYPE_PERCENTAGE
            owed_by_user = compute_percentage_split(
                amount,
                parse_member_amount_fields(form, member_ids, "split_pct"),
            )
        elif split_method == "shares":
            split_type = SPLIT_TYPE_SHARES
            owed_by_user = compute_shares_split(
                amount,
                parse_member_amount_fields(form, member_ids, "split_shares"),
            )
        else:
            owed_by_user = compute_equal_split(amount, member_ids)

        assert_split_covers_total(amount, owed_by_user)

        expense.paid_by = payer_user_id
        expense.category = category
        expense.description = description
        expense.amount = quantize_money(amount)
        expense.remarks = remarks
        expense.split_type = split_type
        expense.tax_tip_amount = ZERO
        expense.self_service_items = False
        if expense_date:
            expense.created_at = expense_date

        if trip_id is None:
            persist_expense_participants(expense.id, member_ids)

        _persist_expense_splits(expense.id, owed_by_user)

    links = create_expense_payment_links(
        expense,
        owed_by_user,
        db.session,
        ExpensePaymentLink,
    )

    is_rec, interval, next_dt, end_dt = parse_recurrence_from_form(form)
    expense.is_recurring = is_rec
    expense.recurrence_interval = interval
    expense.next_occurrence_date = next_dt
    expense.recurrence_end_date = end_dt

    if receipt_file and receipt_file.filename:
        expense.receipt_image_url = save_receipt_fn(app, receipt_file)

    notify_expense_updated(expense, payer_user_id)
    notify_settlement_links_created(links)
    return expense


def expense_edit_url(expense: Expense) -> str:
    from flask import url_for

    if expense.trip_id:
        return url_for(
            "edit_expense",
            trip_id=expense.trip_id,
            expense_id=expense.id,
        )
    return url_for("edit_expense_standalone", expense_id=expense.id)


def expense_detail_url(expense: Expense) -> str:
    from flask import url_for

    if expense.trip_id:
        return url_for(
            "expense_detail",
            trip_id=expense.trip_id,
            expense_id=expense.id,
        )
    return url_for("expense_detail_standalone", expense_id=expense.id)
