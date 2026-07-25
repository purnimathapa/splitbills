"""Khalti ePayment integration (guest checkout).

Sandbox vs production
-----------------------
Khalti uses the same API host for both environments; the *secret key prefix* selects
sandbox or live:

  - Test/sandbox:  KHALTI_SECRET_KEY=test_secret_key_...
  - Production:    KHALTI_SECRET_KEY=live_secret_key_...

Get test keys from https://test-admin.khalti.com (merchant dashboard).
Get live keys from https://admin.khalti.com when you go to production.

Docs: https://docs.khalti.com/khalti-epayment/

Never mark a payment as paid based only on the browser redirect query string.
Always confirm with the server-side lookup API (used here and in the webhook).
"""

from __future__ import annotations

import importlib
from typing import Any

# Same URL for test and live; authorization key determines environment.
KHALTI_API_BASE = "https://khalti.com/api/v2"
KHALTI_INITIATE_URL = f"{KHALTI_API_BASE}/epayment/initiate/"
KHALTI_LOOKUP_URL = f"{KHALTI_API_BASE}/epayment/lookup/"


def khalti_configured(app) -> bool:
    return bool(app.config.get("KHALTI_SECRET_KEY"))


def khalti_purchase_order_id(payment_link_id: int) -> str:
    """Stable id sent to Khalti so webhooks/lookup can map back to our row."""
    return f"splitbills-link-{payment_link_id}"


def parse_purchase_order_link_id(purchase_order_id: str) -> int | None:
    prefix = "splitbills-link-"
    if not purchase_order_id or not purchase_order_id.startswith(prefix):
        return None
    try:
        return int(purchase_order_id[len(prefix) :])
    except ValueError:
        return None


def initiate_khalti_payment(
    *,
    secret_key: str,
    amount_rupees: float,
    purchase_order_id: str,
    purchase_order_name: str,
    return_url: str,
    website_url: str,
    customer_name: str,
) -> dict[str, Any]:
    """Create a Khalti payment session; redirect the guest to payment_url."""
    requests = importlib.import_module("requests")
    amount_paisa = int(round(amount_rupees * 100))
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


def lookup_khalti_payment(secret_key: str, pidx: str) -> dict[str, Any]:
    """Fetch authoritative payment status from Khalti (source of truth)."""
    requests = importlib.import_module("requests")
    response = requests.post(
        KHALTI_LOOKUP_URL,
        headers={"Authorization": f"Key {secret_key}"},
        json={"pidx": pidx},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def confirm_khalti_for_payment_link(
    *,
    secret_key: str,
    pidx: str,
    payment_link_id: int,
    amount_owed_rupees: float,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Verify pidx with Khalti and ensure it matches this guest link.

    Returns (ok, message, lookup_payload).
    """
    try:
        data = lookup_khalti_payment(secret_key, pidx)
    except Exception as exc:
        return False, f"lookup_failed:{exc}", None

    if data.get("status") != "Completed":
        return False, f"not_completed:{data.get('status')}", data

    expected_paisa = int(round(amount_owed_rupees * 100))
    paid_paisa = int(data.get("total_amount") or 0)
    if paid_paisa != expected_paisa:
        return False, f"amount_mismatch:{paid_paisa}!={expected_paisa}", data

    expected_order = khalti_purchase_order_id(payment_link_id)
    order_id = data.get("purchase_order_id")
    if not order_id and isinstance(data.get("purchase_order"), dict):
        order_id = data["purchase_order"].get("purchase_order_id")
    if order_id != expected_order:
        return False, f"order_mismatch:{order_id}!={expected_order}", data

    return True, "confirmed", data


def verify_khalti_payment(secret_key: str, pidx: str) -> bool:
    """Backward-compatible helper: True if Khalti reports Completed."""
    try:
        data = lookup_khalti_payment(secret_key, pidx)
    except Exception:
        return False
    return data.get("status") == "Completed"
