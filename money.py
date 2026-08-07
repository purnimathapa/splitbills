"""Decimal money helpers and SQLAlchemy column types.

All persisted amounts use NUMERIC columns and Python Decimal.
Convert to int/float only at payment-gateway boundaries (paisa/cents).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy.types import Numeric

# Two decimal places — standard currency precision.
MONEY_QUANT = Decimal("0.01")
MONEY_EPSILON = Decimal("0.01")
SPLIT_TOTAL_TOLERANCE = Decimal("0.01")
PERCENTAGE_TOTAL_TOLERANCE = Decimal("0.05")

# SQLAlchemy column types for models.
MONEY_COLUMN = Numeric(12, 2)
QUANTITY_COLUMN = Numeric(10, 4)
PERCENT_COLUMN = Numeric(8, 4)
ZERO = Decimal("0")


def to_decimal(value: Decimal | float | int | str | None) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Amounts must be valid numbers.") from exc


def quantize_money(value: Decimal | float | int | str | None) -> Decimal:
    """Round to cents — use before persisting or comparing monetary totals."""
    return to_decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def fix_rounding_drift(shares: dict[int, Decimal], target_total: Decimal) -> None:
    """Assign leftover cents to the first bucket so shares sum to target_total."""
    drift = target_total - sum(shares.values(), ZERO)
    drift = drift.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    if abs(drift) >= Decimal("0.005") and shares:
        first_key = next(iter(shares))
        shares[first_key] = (shares[first_key] + drift).quantize(
            MONEY_QUANT, rounding=ROUND_HALF_UP
        )


def split_sum_matches_total(
    shares: dict[int, Decimal] | dict[int, float],
    total: Decimal,
    *,
    tolerance: Decimal = SPLIT_TOTAL_TOLERANCE,
) -> bool:
    split_sum = sum((quantize_money(v) for v in shares.values()), ZERO)
    return abs(split_sum - quantize_money(total)) <= tolerance


def assert_split_covers_total(
    total: Decimal,
    owed_by_user: dict[int, Decimal] | dict[int, float],
) -> None:
    if not owed_by_user:
        return
    if not split_sum_matches_total(owed_by_user, total):
        split_sum = sum((quantize_money(v) for v in owed_by_user.values()), ZERO)
        raise ValueError(
            f"Split amounts must add up to {quantize_money(total)} "
            f"(currently {quantize_money(split_sum)})."
        )


def to_smallest_currency_unit(amount: Decimal | float | int | str | None) -> int:
    """Rupees/dollars → paisa/cents for Khalti/Stripe (no float intermediate)."""
    return int(quantize_money(amount) * 100)


# Backward-compatible alias used across the codebase.
round_money = quantize_money
