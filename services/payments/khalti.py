"""Khalti ePayment API (guest checkout).

Docs: https://docs.khalti.com/khalti-epayment/
Never mark paid from the browser redirect alone — always use lookup API.
"""

from __future__ import annotations

import importlib
from decimal import Decimal
from typing import Any

from money import to_smallest_currency_unit

KHALTI_API_BASE = "https://khalti.com/api/v2"
KHALTI_INITIATE_URL = f"{KHALTI_API_BASE}/epayment/initiate/"
KHALTI_LOOKUP_URL = f"{KHALTI_API_BASE}/epayment/lookup/"


def is_configured(secret_key: str | None) -> bool:
    return bool(secret_key)


def purchase_order_id(payment_link_id: int) -> str:
    return f"splitbills-link-{payment_link_id}"


def parse_purchase_order_link_id(purchase_order_id: str) -> int | None:
    prefix = "splitbills-link-"
    if not purchase_order_id or not purchase_order_id.startswith(prefix):
        return None
    try:
        return int(purchase_order_id[len(prefix) :])
    except ValueError:
        return None


def initiate_payment(
    *,
    secret_key: str,
    amount_rupees: Decimal | float,
    purchase_order_id: str,
    purchase_order_name: str,
    return_url: str,
    website_url: str,
    customer_name: str,
) -> dict[str, Any]:
    requests = importlib.import_module("requests")
    amount_paisa = to_smallest_currency_unit(amount_rupees)
    if amount_paisa < 1000:
        raise ValueError("Khalti requires at least Rs 10 (1000 paisa).")

    response = requests.post(
        KHALTI_INITIATE_URL,
        headers={"Authorization": f"Key {secret_key}"},
        json={
            "return_url": return_url,
            "website_url": website_url,
            "amount": amount_paisa,
            "purchase_order_id": purchase_order_id,
            "purchase_order_name": purchase_order_name,
            "customer_info": {"name": customer_name},
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("payment_url") or not data.get("pidx"):
        raise RuntimeError("Khalti did not return payment_url and pidx.")
    return data


def lookup_payment(secret_key: str, pidx: str) -> dict[str, Any]:
    requests = importlib.import_module("requests")
    response = requests.post(
        KHALTI_LOOKUP_URL,
        headers={"Authorization": f"Key {secret_key}"},
        json={"pidx": pidx},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def confirm_for_payment_link(
    *,
    secret_key: str,
    pidx: str,
    payment_link_id: int,
    amount_owed_rupees: Decimal | float,
) -> tuple[bool, str, dict[str, Any] | None]:
    try:
        data = lookup_payment(secret_key, pidx)
    except Exception as exc:
        return False, f"lookup_failed:{exc}", None

    if data.get("status") != "Completed":
        return False, f"not_completed:{data.get('status')}", data

    expected_paisa = to_smallest_currency_unit(amount_owed_rupees)
    paid_paisa = int(data.get("total_amount") or 0)
    if paid_paisa != expected_paisa:
        return False, f"amount_mismatch:{paid_paisa}!={expected_paisa}", data

    expected_order = purchase_order_id(payment_link_id)
    order_id = data.get("purchase_order_id")
    if not order_id and isinstance(data.get("purchase_order"), dict):
        order_id = data["purchase_order"].get("purchase_order_id")
    if order_id != expected_order:
        return False, f"order_mismatch:{order_id}!={expected_order}", data

    return True, "confirmed", data


def verify_webhook_secret(
    configured_secret: str,
    incoming_secret: str,
) -> bool:
    if not configured_secret:
        return False
    return incoming_secret == configured_secret
