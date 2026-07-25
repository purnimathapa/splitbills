"""Shared expense creation from add-expense form payload."""

from __future__ import annotations

from typing import TYPE_CHECKING

from expense_participants import persist_expense_participants
from expense_split_logic import (
    compute_equal_split,
    compute_exact_split,
    compute_itemized_split,
    compute_percentage_split,
    compute_shares_split,
    parse_itemized_form,
    parse_member_amount_fields,
    round_money,
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
    ExpensePaymentLink,
    ExpenseSplit,
    db,
)
from payment_links import create_expense_payment_links
from recurring_expenses import parse_recurrence_from_form

if TYPE_CHECKING:
    from flask import Flask


def _persist_expense_splits(expense_id: int, owed_by_user: dict[int, float]) -> None:
    for user_id, amount_owed in owed_by_user.items():
        if amount_owed <= 0:
            continue
        db.session.add(
            ExpenseSplit(
                expense_id=expense_id,
                user_id=user_id,
                amount_owed=round_money(amount_owed),
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
) -> Expense:
    """Create expense, splits, items, payment links, optional receipt."""
    if len(member_ids) < 2:
        raise ValueError("Pick at least one other person to split with.")

    description = form.get("description", "").strip()
    remarks = form.get("remarks", "").strip()
    use_itemized = form.get("use_itemized") == "on"
    self_service = form.get("self_service_items") == "on" and use_itemized
    member_id_set = set(member_ids)

    owed_by_user: dict[int, float] = {}

    if use_itemized:
        if self_service:
            line_items, tax_tip_amount = parse_itemized_line_items_only(
                form, member_id_set
            )
            items_subtotal = round_money(
                sum(i.price * i.quantity for i in line_items)
            )
            owed_by_user = {}
        else:
            line_items, tax_tip_amount = parse_itemized_form(form, member_id_set)
            owed_by_user, items_subtotal = compute_itemized_split(
                line_items,
                tax_tip_amount,
                member_ids,
            )
        amount = round_money(items_subtotal + tax_tip_amount)

        if not description:
            raise ValueError("Enter a description for this expense.")
        if amount <= 0:
            raise ValueError("Add line items so the total is greater than zero.")

        expense = Expense(
            trip_id=trip_id,
            paid_by=payer_user_id,
            category="General",
            description=description,
            amount=amount,
            remarks=remarks,
            split_type=SPLIT_TYPE_ITEMIZED,
            tax_tip_amount=round_money(tax_tip_amount),
            self_service_items=self_service,
        )
        db.session.add(expense)
        db.session.flush()

        if trip_id is None:
            persist_expense_participants(expense.id, member_ids)

        for parsed_item in line_items:
            item = ExpenseItem(
                expense_id=expense.id,
                name=parsed_item.name,
                price=parsed_item.price,
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
            _persist_expense_splits(expense.id, owed_by_user)
    else:
        split_method = form.get("split_method", "equal").strip()
        amount_raw = (
            form.get("amount", "")
            or form.get("amount_exact", "")
            or form.get("amount_pct", "")
            or form.get("amount_shares", "")
            or "0"
        ).strip()
        try:
            amount = float(amount_raw)
        except ValueError:
            amount = 0

        if not description or amount <= 0:
            raise ValueError("Enter a description and a valid amount.")

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

        expense = Expense(
            trip_id=trip_id,
            paid_by=payer_user_id,
            category="General",
            description=description,
            amount=amount,
            remarks=remarks,
            split_type=split_type,
            tax_tip_amount=0.0,
        )
        db.session.add(expense)
        db.session.flush()

        if trip_id is None:
            persist_expense_participants(expense.id, member_ids)

        _persist_expense_splits(expense.id, owed_by_user)

    if getattr(expense, "self_service_items", False):
        create_self_service_payment_links(
            expense,
            member_ids,
            db.session,
            ExpensePaymentLink,
        )
    else:
        create_expense_payment_links(
            expense,
            owed_by_user,
            db.session,
            ExpensePaymentLink,
        )

    is_rec, interval, next_dt = parse_recurrence_from_form(form)
    expense.is_recurring = is_rec
    expense.recurrence_interval = interval
    expense.next_occurrence_date = next_dt

    if receipt_file and receipt_file.filename:
        expense.receipt_image_url = save_receipt_fn(app, receipt_file)

    log_created_fn(expense, payer_user_id)
    return expense


def expense_detail_url(expense: Expense) -> str:
    from flask import url_for

    if expense.trip_id:
        return url_for(
            "expense_detail",
            trip_id=expense.trip_id,
            expense_id=expense.id,
        )
    return url_for("expense_detail_standalone", expense_id=expense.id)
