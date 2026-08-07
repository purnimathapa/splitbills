from collections import defaultdict

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from activity_log import log_expense_created
from expense_create import (
    create_expense_from_form,
    expense_detail_url,
    expense_edit_url,
    update_expense_from_form,
)
from expense_participants import (
    get_expense_member_ids,
    get_expense_members,
    parse_participant_ids_from_form,
    parse_payer_from_form,
    user_can_access_expense,
    user_can_modify_expense,
)
from group_permissions import PERM_ADD_EXPENSE, PERM_VIEW_EXPENSE
from item_claims import (
    assignments_by_item_id,
    claim_status_for_expense,
    finalize_expense_claims,
)
from money import quantize_money, to_decimal
from models import (
    SPLIT_TYPE_EXACT,
    SPLIT_TYPE_ITEMIZED,
    SPLIT_TYPE_PERCENTAGE,
    SPLIT_TYPE_SHARES,
    Expense,
    ExpensePaymentLink,
    ExpenseSplit,
    Trip,
    TripMember,
    User,
    db,
)
from notifications import notify_expense_updated, notify_settlement_links_created
from receipt_ocr import scan_receipt_image, tesseract_is_available
from receipt_upload import (
    ReceiptValidationError,
    sanitize_receipt_bytes,
    save_receipt_file,
    validate_receipt_file,
    validate_receipt_bytes,
)
from services.balances import build_expense_summaries, get_all_friends, get_global_settlements, get_split_friend_candidates
from services.guest_payments import (
    build_guest_claim_url_for_link,
    build_guest_payment_url_for_link,
)
from services.trip_access import (
    get_trip_members,
    get_user_expenses,
    require_trip_permission,
)
from services.user_messages import GENERIC_ERROR, user_facing_error
from settle_suggestions import find_pending_payment_link, settle_suggestion_for_friend


def _scan_receipt_json_response():
    if not current_app.config.get("RECEIPT_OCR_ENABLED", True):
        return jsonify(
            {
                "success": False,
                "confidence": "none",
                "message": "Receipt scanning is disabled.",
                "ocr_available": False,
                "items": [],
            }
        )

    receipt_file = request.files.get("receipt")
    if not receipt_file or not receipt_file.filename:
        return jsonify(
            {
                "success": False,
                "confidence": "none",
                "message": "Choose a receipt image first.",
                "items": [],
            }
        ), 400

    try:
        max_bytes = current_app.config["RECEIPT_MAX_BYTES"]
        validate_receipt_file(receipt_file, max_bytes)
        raw_bytes = receipt_file.read()
        validate_receipt_bytes(raw_bytes, max_bytes)
        image_bytes = sanitize_receipt_bytes(raw_bytes)
    except ReceiptValidationError as exc:
        return jsonify(
            {"success": False, "confidence": "none", "message": user_facing_error(exc), "items": []}
        ), 400
    except ValueError as exc:
        return jsonify(
            {"success": False, "confidence": "none", "message": user_facing_error(exc), "items": []}
        ), 400
    except Exception:
        return jsonify(
            {"success": False, "confidence": "none", "message": GENERIC_ERROR, "items": []}
        ), 500

    tesseract_cmd = current_app.config.get("TESSERACT_CMD") or None
    result = scan_receipt_image(image_bytes, tesseract_cmd=tesseract_cmd)
    return jsonify(result.to_dict())


def _render_expense_detail(expense: Expense, trip: Trip | None):
    members = get_expense_members(expense)
    members_by_id = {member.id: member for member in members}

    splits = ExpenseSplit.query.filter_by(expense_id=expense.id).all()
    split_rows = []
    for split in splits:
        user = members_by_id.get(split.user_id) or User.query.get(split.user_id)
        if user:
            split_rows.append(
                {
                    "user": user,
                    "amount_owed": split.amount_owed,
                    "percentage": split.percentage,
                    "shares": split.shares,
                }
            )

    payment_links = ExpensePaymentLink.query.filter_by(expense_id=expense.id).all()
    pay_links = []
    claim_links = []
    for link in payment_links:
        guest = members_by_id.get(link.user_id) or link.user
        if getattr(expense, "self_service_items", False) and not expense.claims_finalized_at:
            claim_links.append(
                {
                    "guest_name": guest.name if guest else "Guest",
                    "claimed": bool(link.items_claimed_at),
                    "url": build_guest_claim_url_for_link(link),
                }
            )
        pay_links.append(
            {
                "guest_name": guest.name if guest else "Guest",
                "amount": link.amount_owed,
                "status": link.status,
                "url": build_guest_payment_url_for_link(link),
            }
        )

    member_ids = [m.id for m in members]
    claim_status = None
    line_items = []
    if getattr(expense, "self_service_items", False):
        claim_status = claim_status_for_expense(expense, member_ids)
        assignment_map = assignments_by_item_id(expense)
        for item in expense.items:
            assignee_ids = assignment_map.get(item.id, [])
            assignees = [
                members_by_id.get(uid) or User.query.get(uid) for uid in assignee_ids
            ]
            line_items.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "total": quantize_money(to_decimal(item.price) * to_decimal(item.quantity)),
                    "assignees": [u.name for u in assignees if u],
                    "unclaimed": len(assignee_ids) == 0,
                }
            )
    elif expense.split_type == SPLIT_TYPE_ITEMIZED:
        for item in expense.items:
            assignees = [
                members_by_id.get(a.user_id) or User.query.get(a.user_id)
                for a in item.assignments
            ]
            line_items.append(
                {
                    "name": item.name,
                    "total": quantize_money(to_decimal(item.price) * to_decimal(item.quantity)),
                    "assignees": [u.name for u in assignees if u],
                }
            )

    return render_template(
        "expense_detail.html",
        trip=trip,
        expense=expense,
        split_rows=split_rows,
        pay_links=pay_links,
        claim_links=claim_links,
        claim_status=claim_status,
        line_items=line_items,
        can_edit=user_can_modify_expense(current_user, expense),
        edit_url=expense_edit_url(expense),
    )


def _standalone_allowed_user_ids() -> set[int]:
    allowed = {current_user.id}
    for friend in get_split_friend_candidates():
        allowed.add(friend.id)
    return allowed


    return allowed


def _group_participant_ids(form, trip_members) -> list[int]:
    if form.getlist("participant_user_ids"):
        payer_guess = int(form.get("paid_by_user_id") or current_user.id)
        return parse_participant_ids_from_form(form, payer_guess)
    return [member.id for member in trip_members]


def _split_form_values(expense: Expense) -> dict[str, float]:
    values: dict[str, float] = {}
    for split in expense.splits:
        if expense.split_type == SPLIT_TYPE_EXACT:
            values[f"exact_{split.user_id}"] = float(split.amount_owed or 0)
        elif expense.split_type == SPLIT_TYPE_PERCENTAGE:
            total = float(expense.amount or 0)
            for split in expense.splits:
                if split.percentage:
                    values[f"pct_{split.user_id}"] = float(split.percentage)
                elif total > 0:
                    values[f"pct_{split.user_id}"] = round(
                        float(split.amount_owed or 0) / total * 100, 2
                    )
        elif expense.split_type == SPLIT_TYPE_SHARES and split.shares:
            values[f"shares_{split.user_id}"] = float(split.shares)
    return values


def _edit_line_items(expense: Expense) -> list[dict]:
    items = []
    for item in expense.items:
        items.append(
            {
                "name": item.name,
                "price": float(item.price or 0),
                "quantity": float(item.quantity or 1),
                "assigned_user_ids": [a.user_id for a in item.assignments],
            }
        )
    return items


def _edit_form_context(expense: Expense, members: list[User]) -> dict:
    return {
        "expense": expense,
        "members": members,
        "form_participant_ids": get_expense_member_ids(expense),
        "form_split_method": expense.split_type,
        "form_paid_by": expense.paid_by,
        "form_split_values": _split_form_values(expense),
        "edit_line_items": _edit_line_items(expense)
        if expense.split_type == SPLIT_TYPE_ITEMIZED
        else [],
    }


def _submit_expense_form(
    form,
    receipt_file,
    *,
    payer_user_id: int,
    member_ids: list[int],
    trip_id: int | None,
    allowed_user_ids: set[int] | None,
    success_message: str,
    redirect_on_error: str,
):
    try:
        expense = create_expense_from_form(
            form,
            receipt_file,
            payer_user_id=payer_user_id,
            member_ids=member_ids,
            trip_id=trip_id,
            app=current_app._get_current_object(),
            save_receipt_fn=save_receipt_file,
            log_created_fn=log_expense_created,
            allowed_user_ids=allowed_user_ids,
        )
        db.session.commit()
        flash(success_message, "success")
        return redirect(expense_detail_url(expense))
    except ValueError as exc:
        db.session.rollback()
        flash(user_facing_error(exc), "error")
        return redirect(redirect_on_error)


def _submit_expense_update(
    expense: Expense,
    form,
    receipt_file,
    *,
    member_ids: list[int],
    trip_id: int | None,
    allowed_user_ids: set[int] | None,
    redirect_on_error: str,
):
    try:
        update_expense_from_form(
            expense,
            form,
            receipt_file,
            payer_user_id=expense.paid_by,
            member_ids=member_ids,
            trip_id=trip_id,
            app=current_app._get_current_object(),
            save_receipt_fn=save_receipt_file,
            allowed_user_ids=allowed_user_ids,
        )
        db.session.commit()
        flash("Expense updated.", "success")
        return redirect(expense_detail_url(expense))
    except ValueError as exc:
        db.session.rollback()
        flash(user_facing_error(exc), "error")
        return redirect(redirect_on_error)


def _finalize_expense_claims_handler(expense: Expense):
    if not user_can_modify_expense(current_user, expense):
        flash("Only the person who paid can finalize claims.", "error")
        return redirect(expense_detail_url(expense))

    member_ids = get_expense_member_ids(expense)
    try:
        finalize_expense_claims(expense, member_ids)
        links = ExpensePaymentLink.query.filter_by(expense_id=expense.id).all()
        notify_expense_updated(
            expense,
            current_user.id,
            event_key=str(int(expense.claims_finalized_at.timestamp())),
        )
        notify_settlement_links_created(links)
        db.session.commit()
        flash("Everyone's shares are calculated — payment links are ready to send.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(user_facing_error(exc), "error")
    return redirect(expense_detail_url(expense))


def register(app):
    @app.route("/expenses")
    @login_required
    def expenses():
        trips, expenses = get_user_expenses()
        trip_names = {trip.id: trip.trip_name for trip in trips}
        totals_by_trip = defaultdict(float)
        counts_by_trip = defaultdict(int)
        standalone_total = 0.0
        standalone_count = 0

        for expense in expenses:
            if expense.trip_id is None:
                standalone_total += expense.amount or 0
                standalone_count += 1
                continue
            totals_by_trip[expense.trip_id] += expense.amount or 0
            counts_by_trip[expense.trip_id] += 1

        trip_summaries = [
            {
                "trip": trip,
                "total": round(totals_by_trip[trip.id], 2),
                "count": counts_by_trip[trip.id],
            }
            for trip in trips
            if counts_by_trip[trip.id] > 0
        ]
        if standalone_count:
            trip_summaries.insert(
                0,
                {
                    "trip": None,
                    "label": "One-off splits",
                    "total": round(standalone_total, 2),
                    "count": standalone_count,
                },
            )

        total_expenses = round(sum(expense.amount or 0 for expense in expenses), 2)

        expenses_by_month: dict = {}
        month_order: list[str] = []
        for expense in expenses:
            if expense.created_at:
                month_key = expense.created_at.strftime("%Y-%m")
                label = expense.created_at.strftime("%B %Y").upper()
            else:
                month_key = "unknown"
                label = "UNKNOWN"
            if month_key not in expenses_by_month:
                expenses_by_month[month_key] = {"label": label, "expenses": []}
                month_order.append(month_key)
            expenses_by_month[month_key]["expenses"].append(expense)

        expense_summaries = build_expense_summaries(expenses, current_user.id)

        return render_template(
            "expenses.html",
            expenses=expenses,
            trip_names=trip_names,
            trip_summaries=trip_summaries,
            total_expenses=total_expenses,
            expenses_by_month=[(k, expenses_by_month[k]) for k in month_order],
            expense_summaries=expense_summaries,
        )

    @app.route("/expenses/add", methods=["POST"])
    @login_required
    def quick_add_expense():
        member_ids = parse_participant_ids_from_form(request.form, current_user.id)
        payer_id = parse_payer_from_form(request.form, current_user.id, member_ids)
        return _submit_expense_form(
            request.form,
            request.files.get("receipt"),
            payer_user_id=payer_id,
            member_ids=member_ids,
            trip_id=None,
            allowed_user_ids=_standalone_allowed_user_ids(),
            success_message="Expense saved.",
            redirect_on_error=url_for("expenses"),
        )

    @app.route("/settle")
    @login_required
    def settle_up():
        balances = get_global_settlements()
        trips = get_user_trips()
        friends = get_all_friends()
        friends_by_name = {friend.name: friend for friend in friends}

        you_owe_rows = []
        owed_to_you_rows = []
        total_you_owe = 0.0
        total_owed_to_you = 0.0

        for name, amount in balances.items():
            if amount < -0.01:
                row_amount = round(abs(amount), 2)
                total_you_owe += row_amount
                friend = friends_by_name.get(name)
                pay_url = None
                if friend:
                    shared_trip_ids = [
                        trip.id
                        for trip in trips
                        if TripMember.query.filter_by(
                            trip_id=trip.id, user_id=friend.id
                        ).first()
                    ]
                    link = find_pending_payment_link(
                        current_user.id, friend.id, shared_trip_ids
                    )
                    if link:
                        pay_url = build_guest_payment_url_for_link(link)
                you_owe_rows.append({"name": name, "amount": row_amount, "pay_url": pay_url})
            elif amount > 0.01:
                row_amount = round(amount, 2)
                total_owed_to_you += row_amount
                friend = friends_by_name.get(name)
                collect_url = None
                copy_url = None
                if friend:
                    shared_trip_ids = [
                        trip.id
                        for trip in trips
                        if TripMember.query.filter_by(
                            trip_id=trip.id, user_id=friend.id
                        ).first()
                    ]
                    link = find_pending_payment_link(
                        friend.id, current_user.id, shared_trip_ids
                    )
                    if link:
                        copy_url = build_guest_payment_url_for_link(link)
                        collect_url = url_for("collect", highlight=link.id)
                owed_to_you_rows.append(
                    {
                        "name": name,
                        "amount": row_amount,
                        "collect_url": collect_url,
                        "copy_url": copy_url,
                    }
                )

        you_owe_rows.sort(key=lambda row: row["amount"], reverse=True)
        owed_to_you_rows.sort(key=lambda row: row["amount"], reverse=True)

        settle_suggestions = []
        for friend in friends:
            shared_trip_ids = [
                trip.id
                for trip in trips
                if TripMember.query.filter_by(trip_id=trip.id, user_id=friend.id).first()
            ]
            suggestion = settle_suggestion_for_friend(
                friend, shared_trip_ids, viewer_id=current_user.id
            )
            if suggestion:
                settle_suggestions.append(suggestion)

        return render_template(
            "settle_up.html",
            you_owe_rows=you_owe_rows,
            owed_to_you_rows=owed_to_you_rows,
            total_you_owe=round(total_you_owe, 2),
            total_owed_to_you=round(total_owed_to_you, 2),
            settle_suggestions=settle_suggestions,
        )

    @app.route("/splits/new", methods=["GET", "POST"])
    @login_required
    def new_split():
        """One-off receipt split: scan items → guest links (never tied to a group)."""
        friend_candidates = get_split_friend_candidates()

        if request.method == "POST":
            member_ids = parse_participant_ids_from_form(
                request.form, current_user.id
            )
            return _submit_expense_form(
                request.form,
                request.files.get("receipt"),
                payer_user_id=current_user.id,
                member_ids=member_ids,
                trip_id=None,
                allowed_user_ids=_standalone_allowed_user_ids(),
                success_message="Split created — copy each person's link below.",
                redirect_on_error=url_for("new_split"),
            )

        return render_template(
            "new_split.html",
            friend_candidates=friend_candidates,
            expense_form_action=url_for("new_split"),
            scan_receipt_url=url_for("scan_receipt_standalone"),
            ocr_available=tesseract_is_available(current_app.config.get("TESSERACT_CMD") or None),
        )

    @app.route("/splits/scan-receipt", methods=["POST"])
    @login_required
    def scan_receipt_standalone():
        """OCR receipt for the new-split flow (no split group required)."""
        return _scan_receipt_json_response()

    @app.route("/groups/<int:trip_id>/expenses/add", methods=["GET", "POST"])
    @app.route("/trips/<int:trip_id>/expenses/add", methods=["GET", "POST"])
    @login_required
    def add_expense(trip_id):
        trip, _membership = require_trip_permission(trip_id, PERM_ADD_EXPENSE)
        if not trip.is_active:
            abort(403)

        members = get_trip_members(trip.id)
        member_ids = [member.id for member in members]

        if request.method == "POST":
            member_ids = _group_participant_ids(request.form, members)
            payer_id = parse_payer_from_form(
                request.form, current_user.id, {m.id for m in members}
            )
            try:
                expense = create_expense_from_form(
                    request.form,
                    request.files.get("receipt"),
                    payer_user_id=payer_id,
                    member_ids=member_ids,
                    trip_id=trip.id,
                    app=current_app._get_current_object(),
                    save_receipt_fn=save_receipt_file,
                    log_created_fn=log_expense_created,
                )
                db.session.commit()
                if expense.is_recurring:
                    flash(
                        f"Expense added. It will repeat {expense.recurrence_interval} "
                        f"(next on {expense.next_occurrence_date.strftime('%d %b %Y')}).",
                        "success",
                    )
                else:
                    flash("Expense added successfully.", "success")
                return redirect(expense_detail_url(expense))
            except ValueError as exc:
                db.session.rollback()
                flash(user_facing_error(exc), "error")
                return redirect(url_for("group_details", trip_id=trip.id))

        return render_template(
            "add_expense.html",
            trip=trip,
            members=members,
            expense_form_action=url_for("add_expense", trip_id=trip.id),
            scan_receipt_url=url_for("scan_receipt", trip_id=trip.id),
            ocr_available=tesseract_is_available(current_app.config.get("TESSERACT_CMD") or None),
        )

    @app.route("/groups/<int:trip_id>/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
    @app.route("/trips/<int:trip_id>/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_expense(trip_id, expense_id):
        trip, _membership = require_trip_permission(trip_id, PERM_VIEW_EXPENSE)
        expense = Expense.query.filter_by(id=expense_id, trip_id=trip.id).first_or_404()
        if not user_can_modify_expense(current_user, expense):
            flash("Only the person who paid can edit this expense.", "error")
            return redirect(expense_detail_url(expense))

        members = get_trip_members(trip.id)
        if request.method == "POST":
            member_ids = _group_participant_ids(request.form, members)
            payer_id = parse_payer_from_form(
                request.form, expense.paid_by, {m.id for m in members}
            )
            return _submit_expense_update(
                expense,
                request.form,
                request.files.get("receipt"),
                member_ids=member_ids,
                trip_id=trip.id,
                allowed_user_ids=None,
                redirect_on_error=url_for("edit_expense", trip_id=trip.id, expense_id=expense.id),
            )

        ctx = _edit_form_context(expense, members)
        return render_template(
            "edit_expense.html",
            trip=trip,
            expense_form_action=url_for("edit_expense", trip_id=trip.id, expense_id=expense.id),
            scan_receipt_url=url_for("scan_receipt", trip_id=trip.id),
            ocr_available=tesseract_is_available(current_app.config.get("TESSERACT_CMD") or None),
            **ctx,
        )

    @app.route("/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_expense_standalone(expense_id):
        expense = Expense.query.get_or_404(expense_id)
        if not user_can_modify_expense(current_user, expense):
            flash("Only the person who paid can edit this expense.", "error")
            return redirect(expense_detail_url(expense))
        if expense.trip_id:
            return redirect(
                url_for("edit_expense", trip_id=expense.trip_id, expense_id=expense.id)
            )

        members = get_expense_members(expense)
        allowed = _standalone_allowed_user_ids()
        if request.method == "POST":
            payer_id = parse_payer_from_form(request.form, expense.paid_by, allowed)
            member_ids = parse_participant_ids_from_form(request.form, payer_id)
            return _submit_expense_update(
                expense,
                request.form,
                request.files.get("receipt"),
                member_ids=member_ids,
                trip_id=None,
                allowed_user_ids=allowed,
                redirect_on_error=url_for("edit_expense_standalone", expense_id=expense.id),
            )

        ctx = _edit_form_context(expense, members)
        return render_template(
            "edit_expense.html",
            expense_form_action=url_for("edit_expense_standalone", expense_id=expense.id),
            scan_receipt_url=url_for("scan_receipt_standalone"),
            ocr_available=tesseract_is_available(current_app.config.get("TESSERACT_CMD") or None),
            show_friend_picker=True,
            **ctx,
        )

    @app.route("/groups/<int:trip_id>/expenses/scan-receipt", methods=["POST"])
    @app.route("/trips/<int:trip_id>/expenses/scan-receipt", methods=["POST"])
    @login_required
    def scan_receipt(trip_id):
        """OCR a receipt image and return parsed line items (JSON) for form pre-fill."""
        trip, _membership = require_trip_permission(trip_id, PERM_ADD_EXPENSE)
        if not trip.is_active:
            return jsonify({"success": False, "message": "Not allowed."}), 403
        return _scan_receipt_json_response()

    @app.route("/expenses/<int:expense_id>")
    @login_required
    def expense_detail_standalone(expense_id):
        expense = Expense.query.get_or_404(expense_id)
        if not user_can_access_expense(current_user, expense):
            flash("Not allowed.", "error")
            return redirect(url_for("dashboard"))
        trip = Trip.query.get(expense.trip_id) if expense.trip_id else None
        return _render_expense_detail(expense, trip)

    @app.route("/groups/<int:trip_id>/expenses/<int:expense_id>")
    @app.route("/trips/<int:trip_id>/expenses/<int:expense_id>")
    @login_required
    def expense_detail(trip_id, expense_id):
        trip, _membership = require_trip_permission(trip_id, PERM_VIEW_EXPENSE)
        expense = Expense.query.filter_by(id=expense_id, trip_id=trip.id).first_or_404()
        return _render_expense_detail(expense, trip)

    @app.route("/groups/<int:trip_id>/expenses/<int:expense_id>/finalize-claims", methods=["POST"])
    @app.route("/trips/<int:trip_id>/expenses/<int:expense_id>/finalize-claims", methods=["POST"])
    @login_required
    def finalize_claims_route(trip_id, expense_id):
        trip, _membership = require_trip_permission(trip_id, PERM_VIEW_EXPENSE)
        expense = Expense.query.filter_by(id=expense_id, trip_id=trip.id).first_or_404()
        return _finalize_expense_claims_handler(expense)

    @app.route("/expenses/<int:expense_id>/finalize-claims", methods=["POST"])
    @login_required
    def finalize_claims_standalone(expense_id):
        expense = Expense.query.get_or_404(expense_id)
        if not user_can_access_expense(current_user, expense):
            flash("Not allowed.", "error")
            return redirect(url_for("dashboard"))
        return _finalize_expense_claims_handler(expense)
