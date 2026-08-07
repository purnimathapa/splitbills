"""Guest payment link URLs and token resolution."""

from __future__ import annotations

from flask import current_app

from models import ExpensePaymentLink, db
from payment_links import (
    build_guest_claim_url,
    build_guest_payment_url,
    resolve_payment_link,
)
from services import payment_service


def build_guest_payment_url_for_link(link: ExpensePaymentLink) -> str:
    return build_guest_payment_url(link, current_app.config["SECRET_KEY"])


def build_guest_claim_url_for_link(link: ExpensePaymentLink) -> str:
    return build_guest_claim_url(link, current_app.config["SECRET_KEY"])


def get_payment_link_for_token(token: str) -> ExpensePaymentLink | None:
    return resolve_payment_link(
        token,
        current_app.config["SECRET_KEY"],
        db.session,
        ExpensePaymentLink,
    )


def khalti_checkout_available() -> bool:
    return payment_service.khalti_checkout_available()


def mark_payment_link_paid(*args, **kwargs):
    return payment_service.mark_payment_link_paid(*args, **kwargs)


def settle_link_via_khalti_lookup(link, pidx):
    return payment_service.settle_khalti(link, pidx)


def settle_link_via_stripe_session(link, session_id):
    return payment_service.settle_stripe(link, session_id)


def _request_is_local_dev():
    return payment_service._request_is_local_dev()
