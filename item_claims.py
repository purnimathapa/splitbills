"""Self-service item claiming (uses expense_split_logic.compute_itemized_split)."""

from __future__ import annotations

from datetime import datetime

from expense_split_logic import (
    ParsedLineItem,
    compute_itemized_split,
    round_money,
)
from models import (
    Expense,
    ExpenseItem,
    ExpenseItemAssignment,
    ExpensePaymentLink,
    ExpenseSplit,
    PAYMENT_STATUS_PENDING,
    db,
)


def parse_itemized_line_items_only(form, member_ids: set[int]) -> tuple[list[ParsedLineItem], float]:
    """Parse receipt line items without assignees (self-service mode)."""
    try:
        row_count = int(form.get("item_row_count", "0"))
    except ValueError:
        row_count = 0

    line_items: list[ParsedLineItem] = []
    for index in range(row_count):
        name = form.get(f"item_name_{index}", "").strip()
        if not name:
            continue
        try:
            price = float(form.get(f"item_price_{index}", "0"))
            quantity = float(form.get(f"item_quantity_{index}", "1"))
        except ValueError as exc:
            raise ValueError("Line item price and quantity must be numbers.") from exc

        line_items.append(
            ParsedLineItem(
                name=name,
                price=price,
                quantity=quantity,
                assigned_user_ids=[],
            )
        )

    if not line_items:
        raise ValueError("Add at least one line item.")

    try:
        tax_tip_amount = float(form.get("tax_tip_amount", "0") or "0")
    except ValueError as exc:
        raise ValueError("Tax & tip must be a valid number.") from exc

    return line_items, round_money(max(tax_tip_amount, 0.0))


def assignments_by_item_id(expense: Expense) -> dict[int, list[int]]:
    mapping: dict[int, list[int]] = {}
    for item in expense.items:
        mapping[item.id] = [a.user_id for a in item.assignments]
    return mapping


def build_parsed_items(
    expense: Expense,
    assignment_map: dict[int, list[int]],
) -> list[ParsedLineItem]:
    items: list[ParsedLineItem] = []
    for item in expense.items:
        items.append(
            ParsedLineItem(
                name=item.name,
                price=float(item.price or 0),
                quantity=float(item.quantity or 1),
                assigned_user_ids=list(assignment_map.get(item.id, [])),
            )
        )
    return items


def items_with_zero_claimers(assignment_map: dict[int, list[int]], expense: Expense) -> list[ExpenseItem]:
    unclaimed = []
    for item in expense.items:
        if not assignment_map.get(item.id):
            unclaimed.append(item)
    return unclaimed


def compute_owed_from_assignment_map(
    expense: Expense,
    member_ids: list[int],
    assignment_map: dict[int, list[int]],
    *,
    allow_unclaimed: bool = False,
) -> dict[int, float]:
    """Split using current assignments; skips unclaimed lines unless allow_unclaimed."""
    filtered_map = dict(assignment_map)
    if not allow_unclaimed:
        for item in expense.items:
            if not filtered_map.get(item.id):
                raise ValueError(f'Item "{item.name}" has no claims yet.')

    parsed = build_parsed_items(expense, filtered_map)
    # Only include items that have at least one assignee
    parsed = [p for p in parsed if p.assigned_user_ids]
    if not parsed:
        raise ValueError("No line items have been claimed yet.")

    owed, _sub = compute_itemized_split(
        parsed,
        float(expense.tax_tip_amount or 0),
        member_ids,
    )
    return owed


def preview_user_total(
    expense: Expense,
    user_id: int,
    selected_item_ids: list[int],
    member_ids: list[int],
) -> float:
    """What this user would owe if they claim selected_item_ids (merged with others' saved claims)."""
    base_map = assignments_by_item_id(expense)
    merged = {item_id: [u for u in uids if u != user_id] for item_id, uids in base_map.items()}

    for item_id in selected_item_ids:
        if item_id not in merged:
            merged[item_id] = []
        if user_id not in merged[item_id]:
            merged[item_id].append(user_id)

    # Include only items that would have assignees after merge
    owed = compute_owed_from_assignment_map(
        expense,
        member_ids,
        merged,
        allow_unclaimed=True,
    )
    return round_money(owed.get(user_id, 0.0))


def save_user_claims(
    expense: Expense,
    user_id: int,
    selected_item_ids: list[int],
    member_ids: list[int],
) -> None:
    if getattr(expense, "claims_finalized_at", None):
        raise ValueError("This expense has already been finalized.")

    valid_ids = {item.id for item in expense.items}
    chosen = {i for i in selected_item_ids if i in valid_ids}
    item_ids = [item.id for item in expense.items]

    if item_ids:
        ExpenseItemAssignment.query.filter(
            ExpenseItemAssignment.expense_item_id.in_(item_ids),
            ExpenseItemAssignment.user_id == user_id,
        ).delete(synchronize_session=False)

    for item in expense.items:
        if item.id in chosen:
            db.session.add(
                ExpenseItemAssignment(
                    expense_item_id=item.id,
                    user_id=user_id,
                )
            )

    link = ExpensePaymentLink.query.filter_by(
        expense_id=expense.id,
        user_id=user_id,
    ).first()
    if link:
        link.items_claimed_at = datetime.utcnow()


def create_self_service_payment_links(
    expense: Expense,
    member_ids: list[int],
    db_session,
    link_model,
) -> list:
    import uuid

    created = []
    for user_id in member_ids:
        if user_id == expense.paid_by:
            continue
        existing = link_model.query.filter_by(
            expense_id=expense.id,
            user_id=user_id,
        ).first()
        if existing:
            created.append(existing)
            continue
        link = link_model(
            link_uuid=str(uuid.uuid4()),
            expense_id=expense.id,
            user_id=user_id,
            amount_owed=0.0,
            status=PAYMENT_STATUS_PENDING,
        )
        db_session.add(link)
        created.append(link)
    return created


def claim_status_for_expense(expense: Expense, member_ids: list[int]) -> dict:
    """Summary for expense creator UI."""
    links = ExpensePaymentLink.query.filter_by(expense_id=expense.id).all()
    assignment_map = assignments_by_item_id(expense)
    unclaimed = items_with_zero_claimers(assignment_map, expense)

    participants = [uid for uid in member_ids if uid != expense.paid_by]
    claimed_user_ids = {link.user_id for link in links if link.items_claimed_at}

    return {
        "self_service": bool(getattr(expense, "self_service_items", False)),
        "finalized": bool(getattr(expense, "claims_finalized_at", None)),
        "unclaimed_items": unclaimed,
        "participants": participants,
        "links": links,
        "claimed_user_ids": claimed_user_ids,
        "all_claimed": len(claimed_user_ids) >= len(participants) and len(participants) > 0,
        "all_items_claimed": len(unclaimed) == 0,
    }


def finalize_expense_claims(expense: Expense, member_ids: list[int]) -> dict[int, float]:
    if not getattr(expense, "self_service_items", False):
        raise ValueError("Not a self-service itemized expense.")
    if getattr(expense, "claims_finalized_at", None):
        raise ValueError("Already finalized.")

    assignment_map = assignments_by_item_id(expense)
    owed = compute_owed_from_assignment_map(
        expense,
        member_ids,
        assignment_map,
        allow_unclaimed=False,
    )

    ExpenseSplit.query.filter_by(expense_id=expense.id).delete(synchronize_session=False)
    for user_id, amount in owed.items():
        if amount <= 0:
            continue
        db.session.add(
            ExpenseSplit(
                expense_id=expense.id,
                user_id=user_id,
                amount_owed=round_money(amount),
            )
        )

    links = ExpensePaymentLink.query.filter_by(expense_id=expense.id).all()
    for link in links:
        if link.user_id == expense.paid_by:
            continue
        link.amount_owed = round_money(owed.get(link.user_id, 0.0))

    expense.claims_finalized_at = datetime.utcnow()
    return owed


def maybe_auto_finalize(expense: Expense, member_ids: list[int]) -> bool:
    status = claim_status_for_expense(expense, member_ids)
    if not status["self_service"] or status["finalized"]:
        return False
    if not status["all_claimed"] or not status["all_items_claimed"]:
        return False
    finalize_expense_claims(expense, member_ids)
    return True
