from decimal import Decimal

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for

from expense_participants import get_expense_member_ids
from item_claims import (
    assignments_by_item_id,
    maybe_auto_finalize,
    preview_user_total,
    save_user_claims,
)
from models import (
    Expense,
    ExpensePaymentLink,
    PAYMENT_STATUS_PAID,
    Trip,
    User,
    db,
)
from money import MONEY_EPSILON, quantize_money, to_decimal
from services import payment_service
from services.guest_payments import get_payment_link_for_token
from services.user_messages import user_facing_error
from services.payment_ui import (
    build_payment_page_context,
    user_safe_checkout_error,
)


def _invalid_pay_response():
    return (
        render_template(
            "pay_guest.html",
            **_payment_template_context(invalid=True, link=None),
        ),
        404,
    )


def _payment_template_context(
    *,
    link,
    invalid=False,
    payment_confirmed=False,
    payment_failed=False,
    payer=None,
    guest=None,
    expense=None,
    trip=None,
    token="",
    khalti_enabled=False,
    stripe_enabled=False,
):
    ctx = build_payment_page_context(
        link=link,
        invalid=invalid,
        payment_confirmed=payment_confirmed,
        payment_failed=payment_failed,
        payer_name=payer.name if payer else None,
        guest_name=guest.name if guest else None,
        expense_description=expense.description if expense else None,
        group_name=trip.trip_name if trip else None,
    )
    ctx.update(
        {
            "invalid": invalid,
            "link": link,
            "expense": expense,
            "trip": trip,
            "payer": payer,
            "guest": guest,
            "token": token,
            "khalti_enabled": khalti_enabled,
            "stripe_enabled": stripe_enabled,
            "payment_confirmed": payment_confirmed,
        }
    )
    return ctx


def _guest_split_page(token: str):
    """Public self-service item pick + pay (same token as guest checkout)."""
    link = get_payment_link_for_token(token)
    if link is None:
        return render_template("claim_items.html", invalid=True), 404

    expense = link.expense
    if expense is None or not getattr(expense, "self_service_items", False):
        return render_template("claim_items.html", invalid=True), 404

    trip = Trip.query.get(expense.trip_id) if expense.trip_id else None
    guest = link.user
    payer = User.query.get(expense.paid_by)
    member_ids = get_expense_member_ids(expense)

    if link.user_id == expense.paid_by:
        return render_template("claim_items.html", invalid=True), 404

    items_payload = []
    user_claimed_ids = set()
    assignment_map = assignments_by_item_id(expense)
    for item in expense.items:
        claimers = assignment_map.get(item.id, [])
        if link.user_id in claimers:
            user_claimed_ids.add(item.id)
        others = [
            User.query.get(uid).name
            for uid in claimers
            if uid != link.user_id and User.query.get(uid)
        ]
        items_payload.append(
            {
                "id": item.id,
                "name": item.name,
                "line_total": quantize_money(to_decimal(item.price or 0) * to_decimal(item.quantity or 1)),
                "quantity": float(to_decimal(item.quantity or 1)),
                "shared_with": others,
            }
        )

    confirmed = bool(link.items_claimed_at)
    finalized = bool(getattr(expense, "claims_finalized_at", None))
    show_pay = (
        finalized
        and link.status != PAYMENT_STATUS_PAID
        and quantize_money(link.amount_owed or 0) > MONEY_EPSILON
    )

    if request.method == "POST":
        if finalized and link.status == PAYMENT_STATUS_PAID:
            return redirect(url_for("guest_split", token=token, payment_confirmed="1"))
        if finalized and not show_pay:
            return redirect(url_for("guest_split", token=token))
        try:
            raw_ids = request.form.getlist("item_ids")
            selected = [int(x) for x in raw_ids if str(x).isdigit()]
            save_user_claims(expense, link.user_id, selected, member_ids)
            maybe_auto_finalize(expense, member_ids)
            db.session.commit()
            link = ExpensePaymentLink.query.get(link.id)
            expense = Expense.query.get(expense.id)
            finalized = bool(expense.claims_finalized_at)
            confirm_pay = request.form.get("confirm_pay") == "1"
            if (
                confirm_pay
                and finalized
                and link
                and quantize_money(link.amount_owed) > MONEY_EPSILON
                and link.status != PAYMENT_STATUS_PAID
            ):
                return redirect(url_for("guest_pay", token=token))
            if confirm_pay and not finalized:
                return redirect(
                    url_for("guest_split", token=token, saved="1", waiting="1")
                )
            return redirect(url_for("guest_split", token=token, saved="1"))
        except ValueError as exc:
            db.session.rollback()
            flash(user_facing_error(exc), "error")
            return redirect(url_for("guest_split", token=token))

    preview_url = url_for("guest_split_preview", token=token)
    payment_confirmed = request.args.get("payment_confirmed") == "1"
    ready_pay = request.args.get("ready_pay") == "1" or (
        show_pay and request.args.get("saved") == "1"
    )

    toast_msg = None
    if payment_confirmed or link.status == PAYMENT_STATUS_PAID:
        toast_msg = "Payment received — thank you!"
    elif request.args.get("saved") == "1" and request.args.get("waiting") == "1":
        toast_msg = "Items saved — we'll notify you when everyone has claimed."
    elif request.args.get("saved") == "1":
        toast_msg = "Your items are saved."

    return render_template(
        "claim_items.html",
        invalid=False,
        link=link,
        expense=expense,
        trip=trip,
        payer=payer,
        guest=guest,
        token=token,
        items=items_payload,
        user_claimed_ids=list(user_claimed_ids),
        confirmed=confirmed,
        finalized=finalized,
        show_pay=show_pay or ready_pay,
        preview_url=preview_url,
        khalti_enabled=payment_service.khalti_configured(),
        stripe_enabled=payment_service.stripe_configured(),
        payment_confirmed=payment_confirmed or link.status == PAYMENT_STATUS_PAID,
        toast_msg=toast_msg,
    )


def register(app):
    @app.route("/split/<path:token>", methods=["GET", "POST"])
    @app.route("/claim/<path:token>", methods=["GET", "POST"])
    def guest_split(token):
        return _guest_split_page(token)

    @app.route("/split/<path:token>/preview", methods=["POST"])
    @app.route("/claim/<path:token>/preview", methods=["POST"])
    def guest_split_preview(token):
        link = get_payment_link_for_token(token)
        if link is None:
            return jsonify({"ok": False, "message": "Invalid link."}), 404
        expense = link.expense
        if expense is None or not getattr(expense, "self_service_items", False):
            return jsonify({"ok": False, "message": "Invalid link."}), 404
        if getattr(expense, "claims_finalized_at", None):
            return jsonify({"ok": False, "message": "Already finalized."}), 400

        data = request.get_json(silent=True) or {}
        raw = data.get("item_ids") or []
        selected = [int(x) for x in raw if str(x).isdigit()]
        member_ids = get_expense_member_ids(expense)
        try:
            total = preview_user_total(expense, link.user_id, selected, member_ids)
        except ValueError as exc:
            return jsonify({"ok": False, "message": user_facing_error(exc)}), 400
        return jsonify({"ok": True, "total": total})

    @app.route("/pay/<path:token>", methods=["GET"])
    def guest_pay(token):
        """Public payment page for a single guest share (no login)."""
        link = get_payment_link_for_token(token)
        if link is None:
            return _invalid_pay_response()

        expense = link.expense
        if expense is None:
            return _invalid_pay_response()

        trip = Trip.query.get(expense.trip_id)
        payer = User.query.get(expense.paid_by)
        guest = link.user

        payment_failed = request.args.get("payment_failed") == "1"
        payment_confirmed = request.args.get("payment_confirmed") == "1"

        pidx = request.args.get("pidx", "").strip()
        if pidx and payment_service.khalti_configured():
            if link.status != PAYMENT_STATUS_PAID:
                result = payment_service.settle_khalti(link, pidx)
                if result.settled:
                    return redirect(
                        url_for("guest_pay", token=token, payment_confirmed="1")
                    )
                return redirect(url_for("guest_pay", token=token, payment_failed="1"))
            return redirect(url_for("guest_pay", token=token, payment_confirmed="1"))

        session_id = request.args.get("session_id", "").strip()
        if (
            session_id
            and payment_service.stripe_configured()
            and link.status != PAYMENT_STATUS_PAID
        ):
            result = payment_service.settle_stripe(link, session_id)
            if result.settled:
                return redirect(
                    url_for("guest_pay", token=token, payment_confirmed="1")
                )
            return redirect(url_for("guest_pay", token=token, payment_failed="1"))

        if request.args.get("cancelled") == "1":
            payment_failed = False

        return render_template(
            "pay_guest.html",
            **_payment_template_context(
                link=link,
                payment_confirmed=payment_confirmed,
                payment_failed=payment_failed,
                payer=payer,
                guest=guest,
                expense=expense,
                trip=trip,
                token=token,
                khalti_enabled=payment_service.khalti_checkout_available(),
                stripe_enabled=payment_service.stripe_configured(),
            ),
        )

    @app.route("/pay/<path:token>/mark-paid", methods=["POST"])
    def guest_pay_mark_paid(token):
        link = get_payment_link_for_token(token)
        if link is None:
            return _invalid_pay_response()
        if link.status == PAYMENT_STATUS_PAID:
            return redirect(
                url_for("guest_pay", token=token, payment_confirmed="1")
            )

        payment_service.mark_payment_link_paid(link, "manual")
        return redirect(url_for("guest_pay", token=token, payment_confirmed="1"))

    @app.route("/pay/<path:token>/pay-now", methods=["POST"])
    @app.route("/pay/<path:token>/khalti", methods=["POST"])
    def guest_pay_now(token):
        """Create a Khalti payment session for the exact amount owed."""
        link = get_payment_link_for_token(token)
        if link is None:
            return _invalid_pay_response()
        if link.status == PAYMENT_STATUS_PAID:
            return redirect(
                url_for("guest_pay", token=token, payment_confirmed="1")
            )

        if not payment_service.khalti_configured():
            if current_app.config.get("KHALTI_DEV_MODE") and payment_service._request_is_local_dev():
                return redirect(url_for("guest_pay_khalti_dev", token=token))
            return redirect(url_for("guest_pay", token=token))

        if quantize_money(link.amount_owed or 0) < Decimal("10"):
            flash(
                "Khalti requires at least Rs 10 per payment. Use cash / bank transfer for smaller amounts.",
                "error",
            )
            return redirect(url_for("guest_pay", token=token))

        expense = link.expense
        guest = link.user
        try:
            checkout = payment_service.start_khalti_checkout(
                link,
                return_url=url_for("guest_pay", token=token, _external=True),
                website_url=url_for("home", _external=True),
                description=expense.description or "Split Bills expense",
                customer_name=guest.name if guest else "Guest",
            )
        except Exception:
            db.session.rollback()
            flash(user_safe_checkout_error(), "error")
            return redirect(url_for("guest_pay", token=token))

        return redirect(checkout.redirect_url)

    @app.route("/pay/<path:token>/khalti-dev", methods=["GET", "POST"])
    def guest_pay_khalti_dev(token):
        """Sandbox Khalti UI for local dev when KHALTI_SECRET_KEY is not set."""
        if not current_app.config.get("KHALTI_DEV_MODE") or not payment_service._request_is_local_dev():
            return _invalid_pay_response()

        link = get_payment_link_for_token(token)
        if link is None:
            return _invalid_pay_response()
        if link.status == PAYMENT_STATUS_PAID:
            return redirect(
                url_for("guest_pay", token=token, payment_confirmed="1")
            )

        expense = link.expense
        guest = link.user

        if request.method == "POST":
            payment_service.mark_payment_link_paid(link, "khalti_dev")
            return redirect(url_for("guest_pay", token=token, payment_confirmed="1"))

        return render_template(
            "pay_khalti_dev.html",
            link=link,
            expense=expense,
            guest=guest,
            token=token,
        )

    @app.route("/pay/<path:token>/stripe", methods=["POST"])
    def guest_pay_stripe(token):
        link = get_payment_link_for_token(token)
        if link is None:
            return _invalid_pay_response()
        if link.status == PAYMENT_STATUS_PAID:
            return redirect(url_for("guest_pay", token=token, payment_confirmed="1"))

        if not payment_service.stripe_configured():
            flash("Card checkout is not configured. Use Khalti or mark as paid.", "error")
            return redirect(url_for("guest_pay", token=token))

        expense = link.expense
        guest = link.user
        try:
            checkout = payment_service.start_stripe_checkout(
                link,
                success_url=url_for("guest_pay", token=token, _external=True)
                + "?session_id={CHECKOUT_SESSION_ID}",
                cancel_url=url_for("guest_pay", token=token, _external=True)
                + "?cancelled=1",
                product_name=expense.description or "Shared expense",
                customer_email=guest.email if guest and guest.email else None,
            )
        except Exception:
            db.session.rollback()
            flash(user_safe_checkout_error(), "error")
            return redirect(url_for("guest_pay", token=token))

        return redirect(checkout.redirect_url)

    @app.route("/webhooks/stripe", methods=["POST"])
    def stripe_webhook():
        body, status = payment_service.process_stripe_webhook(
            request.get_data(),
            request.headers.get("Stripe-Signature", ""),
        )
        return body, status

    @app.route("/webhooks/khalti", methods=["POST"])
    def khalti_webhook():
        payload = request.get_json(silent=True) or {}
        if not payload.get("pidx") and request.form.get("pidx"):
            payload = dict(request.form)
        incoming_secret = (
            request.headers.get("X-Khalti-Webhook-Secret")
            or request.headers.get("X-Webhook-Secret")
            or ""
        ).strip()
        body, status = payment_service.process_khalti_webhook(
            payload,
            webhook_secret_header=incoming_secret,
        )
        return body, status
