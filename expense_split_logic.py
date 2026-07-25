"""Build per-user owed amounts for expenses (equal or itemized)."""

from __future__ import annotations

from dataclasses import dataclass

MONEY_DECIMALS = 2


@dataclass
class ParsedLineItem:
    name: str
    price: float
    quantity: float
    assigned_user_ids: list[int]


def round_money(value: float) -> float:
    return round(value, MONEY_DECIMALS)


def fix_rounding_drift(shares: dict[int, float], target_total: float) -> None:
    """Adjust the first bucket so shares sum exactly to target_total."""
    drift = round_money(target_total - sum(shares.values()))
    if abs(drift) >= 0.005 and shares:
        first_key = next(iter(shares))
        shares[first_key] = round_money(shares[first_key] + drift)


def compute_equal_split(amount: float, member_ids: list[int]) -> dict[int, float]:
    """Split amount equally across all trip members."""
    if amount <= 0 or not member_ids:
        return {}

    share = amount / len(member_ids)
    owed = {user_id: round_money(share) for user_id in member_ids}
    fix_rounding_drift(owed, amount)
    return owed


def compute_itemized_split(
    line_items: list[ParsedLineItem],
    tax_tip_amount: float,
    member_ids: list[int],
) -> tuple[dict[int, float], float]:
    """Compute each member's share from line items plus proportional tax/tip.

    Each line item total (price × quantity) is divided evenly among the users
    assigned to that item. Tax and tip are allocated in proportion to each
    person's subtotal from items (before tax/tip).

    Returns:
        (amount_owed_by_user_id, items_subtotal)
    """
    if not line_items:
        raise ValueError("Add at least one line item or use simple equal split.")

    valid_members = set(member_ids)
    owed: dict[int, float] = {user_id: 0.0 for user_id in member_ids}
    items_subtotal = 0.0

    for item in line_items:
        if not item.name.strip():
            raise ValueError("Every line item needs a name.")
        if item.price <= 0 or item.quantity <= 0:
            raise ValueError(f'Invalid price or quantity for item "{item.name}".')

        assignees = [uid for uid in item.assigned_user_ids if uid in valid_members]
        if not assignees:
            raise ValueError(
                f'Assign at least one trip member to item "{item.name}".'
            )

        line_total = round_money(item.price * item.quantity)
        items_subtotal = round_money(items_subtotal + line_total)
        per_person = round_money(line_total / len(assignees))

        for user_id in assignees:
            owed[user_id] = round_money(owed[user_id] + per_person)

    items_subtotal = round_money(items_subtotal)
    tax_tip_amount = round_money(max(tax_tip_amount, 0.0))

    if tax_tip_amount > 0:
        participants = [uid for uid, share in owed.items() if share > 0]
        if not participants:
            participants = list(member_ids)

        if items_subtotal > 0:
            for user_id in participants:
                share_ratio = owed[user_id] / items_subtotal
                owed[user_id] = round_money(
                    owed[user_id] + tax_tip_amount * share_ratio
                )
        else:
            per_person = round_money(tax_tip_amount / len(participants))
            for user_id in participants:
                owed[user_id] = round_money(owed[user_id] + per_person)

    total = round_money(items_subtotal + tax_tip_amount)
    fix_rounding_drift(owed, total)
    return owed, items_subtotal


def compute_exact_split(
    amount: float, amounts_by_user: dict[int, float]
) -> dict[int, float]:
    if amount <= 0:
        raise ValueError("Enter a valid total amount.")
    cleaned = {
        uid: round_money(max(0.0, val))
        for uid, val in amounts_by_user.items()
        if val and val > 0
    }
    if not cleaned:
        raise ValueError("Enter at least one person's exact amount.")
    total = round_money(sum(cleaned.values()))
    if abs(total - amount) > 0.02:
        raise ValueError(
            f"Exact amounts must add up to {amount:.2f} (currently {total:.2f})."
        )
    fix_rounding_drift(cleaned, amount)
    return cleaned


def compute_percentage_split(
    amount: float, pct_by_user: dict[int, float]
) -> dict[int, float]:
    if amount <= 0:
        raise ValueError("Enter a valid total amount.")
    cleaned = {
        uid: float(pct)
        for uid, pct in pct_by_user.items()
        if pct and pct > 0
    }
    if not cleaned:
        raise ValueError("Enter at least one percentage.")
    total_pct = sum(cleaned.values())
    if abs(total_pct - 100.0) > 0.05:
        raise ValueError("Percentages must add up to 100%.")
    owed = {
        uid: round_money(amount * pct / 100.0) for uid, pct in cleaned.items()
    }
    fix_rounding_drift(owed, amount)
    return owed


def compute_shares_split(
    amount: float, shares_by_user: dict[int, float]
) -> dict[int, float]:
    if amount <= 0:
        raise ValueError("Enter a valid total amount.")
    cleaned = {
        uid: float(sh)
        for uid, sh in shares_by_user.items()
        if sh and sh > 0
    }
    if not cleaned:
        raise ValueError("Enter at least one share count.")
    total_shares = sum(cleaned.values())
    if total_shares <= 0:
        raise ValueError("Enter at least one share count.")
    owed = {
        uid: round_money(amount * sh / total_shares) for uid, sh in cleaned.items()
    }
    fix_rounding_drift(owed, amount)
    return owed


def parse_member_amount_fields(
    form, member_ids: list[int], prefix: str
) -> dict[int, float]:
    values: dict[int, float] = {}
    for user_id in member_ids:
        raw = form.get(f"{prefix}_{user_id}", "").strip()
        if not raw:
            continue
        try:
            values[user_id] = float(raw)
        except ValueError as exc:
            raise ValueError("Split amounts must be valid numbers.") from exc
    return values


def parse_itemized_form(form, member_ids: set[int]) -> tuple[list[ParsedLineItem], float]:
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
            price = float(form.get(f"item_price_{index}", "0"))
            quantity = float(form.get(f"item_quantity_{index}", "1"))
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
        tax_tip_amount = float(form.get("tax_tip_amount", "0") or "0")
    except ValueError as exc:
        raise ValueError("Tax & tip must be a valid number.") from exc

    return line_items, tax_tip_amount
