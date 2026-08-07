"""Shared payment result types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettlementResult:
    """Outcome of confirming or recording a guest payment."""

    settled: bool
    detail: str
    already_paid: bool = False


@dataclass(frozen=True)
class CheckoutResult:
    """Provider checkout session ready for browser redirect."""

    redirect_url: str
    provider_ref: str | None = None
