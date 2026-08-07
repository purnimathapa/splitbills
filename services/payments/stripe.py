"""Stripe Checkout API (guest card payments)."""

from __future__ import annotations

import importlib
from decimal import Decimal
from typing import Any

from money import to_smallest_currency_unit


def is_configured(secret_key: str | None) -> bool:
    return bool(secret_key)


def _stripe():
    return importlib.import_module("stripe")


def create_checkout_session(
    *,
    secret_key: str,
    amount_rupees: Decimal | float,
    currency: str,
    payment_link_id: int,
    product_name: str,
    customer_email: str | None,
    success_url: str,
    cancel_url: str,
) -> dict[str, Any]:
    stripe = _stripe()
    stripe.api_key = secret_key
    unit_amount = to_smallest_currency_unit(amount_rupees)
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


def construct_webhook_event(
    *,
    secret_key: str,
    webhook_secret: str,
    payload: bytes,
    signature: str,
) -> dict[str, Any]:
    stripe = _stripe()
    stripe.api_key = secret_key
    event = stripe.Webhook.construct_event(payload, signature, webhook_secret)
    return dict(event)


def confirm_for_payment_link(
    *,
    secret_key: str,
    session_id: str,
    payment_link_id: int,
    amount_owed_rupees: Decimal | float,
) -> tuple[bool, str, dict[str, Any] | None]:
    try:
        data = retrieve_checkout_session(secret_key, session_id)
    except Exception as exc:
        return False, f"stripe_lookup_failed:{exc}", None

    if data.get("payment_status") != "paid":
        return False, f"not_paid:{data.get('payment_status')}", data

    meta_id = data.get("metadata", {}).get("payment_link_id")
    ref_id = data.get("client_reference_id")
    expected = str(payment_link_id)
    if meta_id != expected and ref_id != expected:
        return False, "link_mismatch", data

    expected_paisa = to_smallest_currency_unit(amount_owed_rupees)
    paid = int(data.get("amount_total") or 0)
    if paid and abs(paid - expected_paisa) > 2:
        return False, f"amount_mismatch:{paid}!={expected_paisa}", data

    return True, "confirmed", data
