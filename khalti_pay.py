"""Backward-compatible re-exports — prefer services.payments.khalti."""

from services.payments import khalti as _khalti
from services.payments.khalti import (
    confirm_for_payment_link as confirm_khalti_for_payment_link,
    initiate_payment as initiate_khalti_payment,
    lookup_payment as lookup_khalti_payment,
    parse_purchase_order_link_id,
    purchase_order_id as khalti_purchase_order_id,
)


def khalti_configured(app) -> bool:
    return _khalti.is_configured(app.config.get("KHALTI_SECRET_KEY"))


def verify_khalti_payment(secret_key: str, pidx: str) -> bool:
    try:
        data = lookup_khalti_payment(secret_key, pidx)
    except Exception:
        return False
    return data.get("status") == "Completed"
