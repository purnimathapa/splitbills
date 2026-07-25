"""Signed guest payment links (one per debtor per expense)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from itsdangerous import BadSignature, URLSafeSerializer

if TYPE_CHECKING:
    from flask import Flask

    from models import Expense, ExpensePaymentLink

PAYMENT_LINK_SALT = "splitbills-guest-payment-v1"
PAYMENT_STATUS_PENDING = "pending"
PAYMENT_STATUS_PAID = "paid"


def get_payment_serializer(secret_key: str) -> URLSafeSerializer:
    return URLSafeSerializer(secret_key, salt=PAYMENT_LINK_SALT)


def build_signed_payment_token(
    secret_key: str,
    link_uuid: str,
    expense_id: int,
    user_id: int,
) -> str:
    """Return an HMAC-signed token bound to one expense + user pair."""
    signer = get_payment_serializer(secret_key)
    return signer.dumps(
        {
            "link_uuid": link_uuid,
            "expense_id": expense_id,
            "user_id": user_id,
        }
    )


def decode_payment_token(secret_key: str, token: str) -> dict[str, Any] | None:
    signer = get_payment_serializer(secret_key)
    try:
        payload = signer.loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    required = {"link_uuid", "expense_id", "user_id"}
    if not required.issubset(payload.keys()):
        return None
    return payload


def build_guest_payment_url(link, secret_key: str) -> str:
    """Full external URL for a guest payment link (requires Flask app context)."""
    from flask import url_for

    token = build_signed_payment_token(
        secret_key,
        link.link_uuid,
        link.expense_id,
        link.user_id,
    )
    return url_for("guest_pay", token=token, _external=True)


def build_guest_claim_url(link, secret_key: str) -> str:
    from flask import url_for

    token = build_signed_payment_token(
        secret_key,
        link.link_uuid,
        link.expense_id,
        link.user_id,
    )
    return url_for("guest_split", token=token, _external=True)


def resolve_payment_link(token: str, secret_key: str, db_session, model_class):
    """Verify signature and load the matching payment link row."""
    payload = decode_payment_token(secret_key, token)
    if payload is None:
        return None

    return (
        db_session.query(model_class)
        .filter_by(
            link_uuid=payload["link_uuid"],
            expense_id=int(payload["expense_id"]),
            user_id=int(payload["user_id"]),
        )
        .first()
    )


def create_expense_payment_links(
    expense: Expense,
    owed_by_user: dict[int, float],
    db_session,
    link_model,
) -> list:
    """Create pending payment links for everyone who owes (except the payer)."""
    created = []
    for user_id, amount_owed in owed_by_user.items():
        if amount_owed <= 0.01:
            continue
        if user_id == expense.paid_by:
            continue

        link_uuid = str(uuid.uuid4())
        link = link_model(
            link_uuid=link_uuid,
            expense_id=expense.id,
            user_id=user_id,
            amount_owed=round(float(amount_owed), 2),
            status=PAYMENT_STATUS_PENDING,
        )
        db_session.add(link)
        created.append(link)
    return created
