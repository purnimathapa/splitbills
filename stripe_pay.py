"""Stripe Checkout for guest payment links (optional, alongside Khalti).

Set STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET in .env.
Amounts are in the app's currency unit (e.g. NPR rupees); Stripe expects smallest currency unit.
For USD use cents; for NPR Stripe supports NPR if enabled on your account — we use * 100 as paisa/cents.
"""

from __future__ import annotations

import importlib
from typing import Any


def stripe_configured(app) -> bool:
    return bool(app.config.get("STRIPE_SECRET_KEY"))


def _stripe():
    stripe = importlib.import_module("stripe")
    return stripe


def create_checkout_session(
    *,
    secret_key: str,
    amount_rupees: float,
    currency: str,
    payment_link_id: int,
    product_name: str,
    customer_email: str | None,
    success_url: str,
    cancel_url: str,
) -> dict[str, Any]:
    stripe = _stripe()
    stripe.api_key = secret_key
    unit_amount = int(round(amount_rupees * 100))
    if unit_amount < 50:
        raise ValueError("Amount too small for card checkout.")

    currency_code = (currency or "usd").lower()
    if currency_code in ("rs", "npr", "रू"):
        currency_code = "npr"

    params: dict[str, Any] = {
        "mode": "payment",
        "success_url": success_url + ("&" if "?" in success_url else "?") + "stripe=success",
        "cancel_url": cancel_url,
        "client_reference_id": str(payment_link_id),
        "metadata": {"payment_link_id": str(payment_link_id)},
        "line_items": [
            {
                "quantity": 1,
                "price_data": {
                    "currency": currency_code,
                    "unit_amount": unit_amount,
                    "product_data": {"name": product_name[:120]},
                },
            }
        ],
    }
    if customer_email:
        params["customer_email"] = customer_email

    session = stripe.checkout.Session.create(**params)
    return {"id": session.id, "url": session.url}


def retrieve_checkout_session(secret_key: str, session_id: str) -> dict[str, Any]:
    stripe = _stripe()
    stripe.api_key = secret_key
    session = stripe.checkout.Session.retrieve(session_id)
    return {
        "id": session.id,
        "payment_status": session.payment_status,
        "client_reference_id": session.client_reference_id,
        "metadata": dict(session.metadata or {}),
        "amount_total": session.amount_total,
    }
