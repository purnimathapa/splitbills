"""Build per-user owed amounts for expenses (equal or itemized).

Returns Decimal shares; callers persist via ``quantize_money`` on model columns.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from money import (
    PERCENTAGE_TOTAL_TOLERANCE,
    SPLIT_TOTAL_TOLERANCE,
    ZERO,
    fix_rounding_drift,
    quantize_money,
    to_decimal,
)

MONEY_QUANT = Decimal("0.01")


@dataclass
class ParsedLineItem:
    name: str
    price: Decimal
    quantity: Decimal
    assigned_user_ids: list[int]


def compute_equal_split(
    amount: Decimal | float | int | str, member_ids: list[int]
) -> dict[int, Decimal]:
    """Split amount equally across all members; sum equals total after drift fix."""
    total = quantize_money(amount)
    if total <= ZERO or not member_ids:
        return {}

    share = (total / len(member_ids)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    owed: dict[int, Decimal] = {user_id: share for user_id in member_ids}
    fix_rounding_drift(owed, total)
    return owed


def compute_itemized_split(
    line_items: list[ParsedLineItem],
    tax_tip_amount: Decimal | float | int | str,
    member_ids: list[int],
) -> tuple[dict[int, Decimal], Decimal]:
    """Line items plus proportional tax/tip; returns (owed_by_user, items_subtotal)."""
    if not line_items:
        raise ValueError("Add at least one line item or use simple equal split.")

    valid_members = set(member_ids)
    owed: dict[int, Decimal] = {user_id: ZERO for user_id in member_ids}
    items_subtotal = ZERO

    for item in line_items:
        if not item.name.strip():
            raise ValueError("Every line item needs a name.")
        price = to_decimal(item.price)
        quantity = to_decimal(item.quantity)
        if price <= ZERO or quantity <= ZERO:
            raise ValueError(f'Invalid price or quantity for item "{item.name}".')

        assignees = [uid for uid in item.assigned_user_ids if uid in valid_members]
        if not assignees:
            raise ValueError(
                f'Assign at least one trip member to item "{item.name}".'
            )

        line_total = (price * quantity).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        items_subtotal += line_total
        per_person = (line_total / len(assignees)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

        for user_id in assignees:
            owed[user_id] += per_person

    items_subtotal = items_subtotal.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    tax_tip = max(to_decimal(tax_tip_amount), ZERO).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

    if tax_tip > ZERO:
        participants = [uid for uid, share in owed.items() if share > ZERO]
        if not participants:
            participants = list(member_ids)

        if items_subtotal > ZERO:
            for user_id in participants:
                share_ratio = owed[user_id] / items_subtotal
                owed[user_id] += (tax_tip * share_ratio).quantize(
                    MONEY_QUANT, rounding=ROUND_HALF_UP
                )
        else:
            per_person = (tax_tip / len(participants)).quantize(
                MONEY_QUANT, rounding=ROUND_HALF_UP
            )
            for user_id in participants:
                owed[user_id] += per_person

    total = (items_subtotal + tax_tip).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    fix_rounding_drift(owed, total)
    return owed, items_subtotal


def compute_exact_split(
    amount: Decimal | float | int | str,
    amounts_by_user: dict[int, Decimal | float | int | str],
) -> dict[int, Decimal]:
    total = quantize_money(amount)
    if total <= ZERO:
        raise ValueError("Enter a valid total amount.")
    cleaned: dict[int, Decimal] = {
        uid: quantize_money(val)
        for uid, val in amounts_by_user.items()
        if val and to_decimal(val) > ZERO
    }
    if not cleaned:
        raise ValueError("Enter at least one person's exact amount.")
    split_sum = sum(cleaned.values(), ZERO)
    if abs(split_sum - total) > SPLIT_TOTAL_TOLERANCE:
        raise ValueError(
            f"Exact amounts must add up to {total} (currently {split_sum})."
        )
    fix_rounding_drift(cleaned, total)
    return cleaned


def compute_percentage_split(
    amount: Decimal | float | int | str,
    pct_by_user: dict[int, Decimal | float | int | str],
) -> dict[int, Decimal]:
    total = quantize_money(amount)
    if total <= ZERO:
        raise ValueError("Enter a valid total amount.")
    cleaned: dict[int, Decimal] = {
        uid: to_decimal(pct)
        for uid, pct in pct_by_user.items()
        if pct and to_decimal(pct) > ZERO
    }
    if not cleaned:
        raise ValueError("Enter at least one percentage.")
    total_pct = sum(cleaned.values(), ZERO)
    if abs(total_pct - Decimal("100")) > PERCENTAGE_TOTAL_TOLERANCE:
        raise ValueError("Percentages must add up to 100%.")
    owed: dict[int, Decimal] = {
        uid: (total * pct / Decimal("100")).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        for uid, pct in cleaned.items()
    }
    fix_rounding_drift(owed, total)
    return owed


def compute_shares_split(
    amount: Decimal | float | int | str,
    shares_by_user: dict[int, Decimal | float | int | str],
) -> dict[int, Decimal]:
    total = quantize_money(amount)
    if total <= ZERO:
        raise ValueError("Enter a valid total amount.")
    cleaned: dict[int, Decimal] = {
        uid: to_decimal(sh)
        for uid, sh in shares_by_user.items()
        if sh and to_decimal(sh) > ZERO
    }
    if not cleaned:
        raise ValueError("Enter at least one share count.")
    total_shares = sum(cleaned.values(), ZERO)
    if total_shares <= ZERO:
        raise ValueError("Enter at least one share count.")
    owed: dict[int, Decimal] = {
        uid: (total * sh / total_shares).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        for uid, sh in cleaned.items()
    }
    fix_rounding_drift(owed, total)
    return owed


def parse_member_amount_fields(
    form, member_ids: list[int], prefix: str
) -> dict[int, Decimal]:
    values: dict[int, Decimal] = {}
    for user_id in member_ids:
        raw = form.get(f"{prefix}_{user_id}", "").strip()
        if not raw:
            continue
        try:
            values[user_id] = quantize_money(to_decimal(raw))
        except ValueError as exc:
            raise ValueError("Split amounts must be valid numbers.") from exc
    return values


def parse_itemized_form(form, member_ids: set[int]) -> tuple[list[ParsedLineItem], Decimal]:
    """Read indexed item fields from a Flask request form."""
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
            price = to_decimal(form.get(f"item_price_{index}", "0"))
            quantity = to_decimal(form.get(f"item_quantity_{index}", "1"))
        except ValueError as exc:
            raise ValueError("Line item price and quantity must be numbers.") from exc

        raw_ids = form.getlist(f"item_users_{index}")
        assigned_user_ids: list[int] = []
        for raw_id in raw_ids:
            try:
                user_id = int(raw_id)
            except ValueError:
                continue
            if user_id in member_ids:
                assigned_user_ids.append(user_id)

        line_items.append(
            ParsedLineItem(
                name=name,
                price=price,
                quantity=quantity,
                assigned_user_ids=assigned_user_ids,
            )
        )

    try:
        tax_tip_amount = quantize_money(to_decimal(form.get("tax_tip_amount", "0") or "0"))
    except ValueError as exc:
        raise ValueError("Tax & tip must be a valid number.") from exc

    return line_items, tax_tip_amount
