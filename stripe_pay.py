"""Backward-compatible re-exports — prefer services.payments.stripe."""

from services.payments import stripe as _stripe
from services.payments.stripe import create_checkout_session, retrieve_checkout_session


def stripe_configured(app) -> bool:
    return _stripe.is_configured(app.config.get("STRIPE_SECRET_KEY"))
