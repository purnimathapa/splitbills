"""User-facing payment page state and safe error copy (no provider secrets)."""

from __future__ import annotations

from models import PAYMENT_STATUS_PAID, ExpensePaymentLink

STATE_PENDING = "pending"
STATE_PROCESSING = "processing"
STATE_SUCCESS = "success"
STATE_FAILED = "failed"
STATE_EXPIRED = "expired"

_STATE_LABELS = {
    STATE_PENDING: "Payment due",
    STATE_PROCESSING: "Confirming payment",
    STATE_SUCCESS: "Payment successful",
    STATE_FAILED: "Payment failed",
    STATE_EXPIRED: "Link expired",
}


def user_safe_checkout_error(_exc: BaseException | None = None) -> str:
    return (
        "We couldn't start the payment. Please try again in a moment, "
        'or pay the person directly and tap "I paid cash / bank transfer".'
    )


def user_safe_settlement_error(_detail: str | None = None) -> str:
    return (
        "We couldn't confirm your payment yet. If money was deducted, "
        "wait a few minutes and refresh this page."
    )


def provider_display_name(provider: str | None) -> str | None:
    if not provider:
        return None
    key = provider.strip().lower()
    if key in ("khalti", "khalti_dev"):
        return "Khalti"
    if key == "stripe":
        return "Card"
    if key == "manual":
        return "Cash or bank transfer"
    return "Online payment"


def build_payment_page_context(
    *,
    link: ExpensePaymentLink | None,
    invalid: bool = False,
    payment_confirmed: bool = False,
    payment_failed: bool = False,
    payer_name: str | None = None,
    guest_name: str | None = None,
    expense_description: str | None = None,
    group_name: str | None = None,
) -> dict:
    """Presentation-only context for guest payment templates."""
    if invalid or link is None:
        return {
            "payment_state": STATE_EXPIRED,
            "payment_state_label": _STATE_LABELS[STATE_EXPIRED],
            "payment_title": "This link isn't valid",
            "payment_message": "This payment link is invalid or has expired.",
            "show_payment_form": False,
        }

    description = expense_description or (link.expense.description if link.expense else "Shared expense")
    recipient = payer_name or "the person who paid"
    payer_label = payer_name or "Recipient"

    if link.status == PAYMENT_STATUS_PAID or payment_confirmed:
        paid_via = provider_display_name(link.payment_provider)
        msg = f"Your share for {description} is settled."
        if paid_via:
            msg = f"Paid via {paid_via}. Your share for {description} is settled."
        return {
            "payment_state": STATE_SUCCESS,
            "payment_state_label": _STATE_LABELS[STATE_SUCCESS],
            "payment_title": "Payment successful",
            "payment_message": msg,
            "show_payment_form": False,
            "amount": float(link.amount_owed or 0),
            "recipient_name": recipient,
            "payer_label": payer_label,
            "expense_description": description,
            "group_name": group_name,
            "guest_name": guest_name,
            "paid_via": paid_via,
            "paid_at": link.paid_at,
        }

    if payment_failed:
        return {
            "payment_state": STATE_FAILED,
            "payment_state_label": _STATE_LABELS[STATE_FAILED],
            "payment_title": "Payment couldn't be completed",
            "payment_message": user_safe_settlement_error(),
            "show_payment_form": True,
            "amount": float(link.amount_owed or 0),
            "recipient_name": recipient,
            "payer_label": payer_label,
            "expense_description": description,
            "group_name": group_name,
            "guest_name": guest_name,
        }

    if link.khalti_pidx or link.stripe_checkout_session_id:
        return {
            "payment_state": STATE_PROCESSING,
            "payment_state_label": _STATE_LABELS[STATE_PROCESSING],
            "payment_title": "Confirming your payment",
            "payment_message": (
                "We're waiting for confirmation from the payment provider. "
                "This usually takes a few seconds — refresh if it doesn't update."
            ),
            "show_payment_form": True,
            "amount": float(link.amount_owed or 0),
            "recipient_name": recipient,
            "payer_label": payer_label,
            "expense_description": description,
            "group_name": group_name,
            "guest_name": guest_name,
        }

    return {
        "payment_state": STATE_PENDING,
        "payment_state_label": _STATE_LABELS[STATE_PENDING],
        "payment_title": "Pay your share",
        "payment_message": f"Settle your portion with {recipient}.",
        "show_payment_form": True,
        "amount": float(link.amount_owed or 0),
        "recipient_name": recipient,
        "payer_label": payer_label,
        "expense_description": description,
        "group_name": group_name,
        "guest_name": guest_name,
    }
