"""Guest payment orchestration — single entry point for routes and webhooks."""

from __future__ import annotations

from datetime import datetime

from flask import current_app, request, url_for

from activity_log import log_payment_confirmed
from models import (
    ExpensePaymentLink,
    PAYMENT_STATUS_PAID,
    db,
)
from notifications import notify_payment_received
from services.payments import khalti as khalti_provider
from services.payments import stripe as stripe_provider
from services.payments.types import CheckoutResult, SettlementResult


def _cfg(key: str, default=""):
    return current_app.config.get(key, default)


def khalti_configured() -> bool:
    return khalti_provider.is_configured(_cfg("KHALTI_SECRET_KEY"))


def stripe_configured() -> bool:
    return stripe_provider.is_configured(_cfg("STRIPE_SECRET_KEY"))


def _request_is_local_dev() -> bool:
    host = (request.host or "").split(":")[0].lower()
    return host in ("127.0.0.1", "localhost", "::1")


def khalti_checkout_available() -> bool:
    if khalti_configured():
        return True
    return bool(_cfg("KHALTI_DEV_MODE")) and _request_is_local_dev()


def mark_payment_link_paid(
    link: ExpensePaymentLink,
    provider: str,
    *,
    khalti_pidx: str | None = None,
    stripe_session_id: str | None = None,
    commit: bool = True,
) -> SettlementResult:
    """Mark guest share settled. Idempotent when already paid."""
    if link.status == PAYMENT_STATUS_PAID:
        return SettlementResult(settled=True, detail="already_paid", already_paid=True)

    link.status = PAYMENT_STATUS_PAID
    link.paid_at = datetime.utcnow()
    link.payment_provider = provider
    if khalti_pidx:
        link.khalti_pidx = khalti_pidx
    if stripe_session_id:
        link.stripe_checkout_session_id = stripe_session_id

    log_payment_confirmed(link, provider)
    notify_payment_received(link)

    if commit:
        db.session.commit()
    return SettlementResult(settled=True, detail="paid")


def settle_khalti(link: ExpensePaymentLink, pidx: str) -> SettlementResult:
    if link.status == PAYMENT_STATUS_PAID:
        return SettlementResult(settled=True, detail="already_paid", already_paid=True)

    ok, message, _payload = khalti_provider.confirm_for_payment_link(
        secret_key=_cfg("KHALTI_SECRET_KEY"),
        pidx=pidx,
        payment_link_id=link.id,
        amount_owed_rupees=link.amount_owed,
    )
    if not ok:
        return SettlementResult(settled=False, detail=message)

    return mark_payment_link_paid(link, "khalti", khalti_pidx=pidx, commit=True)


def settle_stripe(link: ExpensePaymentLink, session_id: str) -> SettlementResult:
    if link.status == PAYMENT_STATUS_PAID:
        return SettlementResult(settled=True, detail="already_paid", already_paid=True)
    if not stripe_configured():
        return SettlementResult(settled=False, detail="stripe_not_configured")

    ok, message, _payload = stripe_provider.confirm_for_payment_link(
        secret_key=_cfg("STRIPE_SECRET_KEY"),
        session_id=session_id,
        payment_link_id=link.id,
        amount_owed_rupees=link.amount_owed,
    )
    if not ok:
        return SettlementResult(settled=False, detail=message)

    return mark_payment_link_paid(
        link,
        "stripe",
        stripe_session_id=session_id,
        commit=True,
    )


def start_khalti_checkout(
    link: ExpensePaymentLink,
    *,
    return_url: str,
    website_url: str,
    description: str,
    customer_name: str,
) -> CheckoutResult:
    data = khalti_provider.initiate_payment(
        secret_key=_cfg("KHALTI_SECRET_KEY"),
        amount_rupees=link.amount_owed,
        purchase_order_id=khalti_provider.purchase_order_id(link.id),
        purchase_order_name=description,
        return_url=return_url,
        website_url=website_url,
        customer_name=customer_name,
    )
    link.khalti_pidx = data["pidx"]
    db.session.commit()
    return CheckoutResult(redirect_url=data["payment_url"], provider_ref=data["pidx"])


def start_stripe_checkout(
    link: ExpensePaymentLink,
    *,
    success_url: str,
    cancel_url: str,
    product_name: str,
    customer_email: str | None,
) -> CheckoutResult:
    currency = _cfg("STRIPE_CURRENCY") or _cfg("DEFAULT_CURRENCY", "npr")
    data = stripe_provider.create_checkout_session(
        secret_key=_cfg("STRIPE_SECRET_KEY"),
        amount_rupees=link.amount_owed,
        currency=currency,
        payment_link_id=link.id,
        product_name=product_name,
        customer_email=customer_email,
        success_url=success_url,
        cancel_url=cancel_url,
    )
    link.stripe_checkout_session_id = data["id"]
    db.session.commit()
    return CheckoutResult(redirect_url=data["url"], provider_ref=data["id"])


def process_stripe_webhook(payload: bytes, signature: str) -> tuple[dict, int]:
    wh_secret = _cfg("STRIPE_WEBHOOK_SECRET", "").strip()
    if not wh_secret:
        return {"error": "webhook not configured"}, 503

    try:
        event = stripe_provider.construct_webhook_event(
            secret_key=_cfg("STRIPE_SECRET_KEY"),
            webhook_secret=wh_secret,
            payload=payload,
            signature=signature,
        )
    except Exception:
        return {"error": "invalid webhook signature"}, 400

    if event.get("type") == "checkout.session.completed":
        sess = event["data"]["object"]
        link_id = (sess.get("metadata") or {}).get("payment_link_id") or sess.get(
            "client_reference_id"
        )
        try:
            link_id_int = int(link_id)
        except (TypeError, ValueError):
            return {"error": "bad link id"}, 400
        link = db.session.get(ExpensePaymentLink, link_id_int)
        if link and link.status != PAYMENT_STATUS_PAID:
            settle_stripe(link, sess["id"])

    return {"received": True}, 200


def process_khalti_webhook(
    payload: dict,
    *,
    webhook_secret_header: str,
) -> tuple[dict, int]:
    configured_secret = _cfg("KHALTI_WEBHOOK_SECRET", "").strip()
    if configured_secret and not khalti_provider.verify_webhook_secret(
        configured_secret, webhook_secret_header
    ):
        return {"error": "unauthorized webhook"}, 401

    pidx = (payload.get("pidx") or "").strip()
    purchase_order_id = (payload.get("purchase_order_id") or "").strip()
    if not pidx:
        return {"error": "missing pidx"}, 400

    link = ExpensePaymentLink.query.filter_by(khalti_pidx=pidx).first()
    if link is None and purchase_order_id:
        link_id = khalti_provider.parse_purchase_order_link_id(purchase_order_id)
        if link_id:
            link = db.session.get(ExpensePaymentLink, link_id)

    if link is None:
        return {"error": "payment link not found"}, 404
    if not khalti_configured():
        return {"error": "khalti not configured"}, 503

    result = settle_khalti(link, pidx)
    if result.settled:
        return {"status": "paid", "detail": result.detail}, 200
    return {"status": "pending", "detail": result.detail}, 202


# Backward-compatible aliases
settle_link_via_khalti_lookup = settle_khalti
settle_link_via_stripe_session = settle_stripe
