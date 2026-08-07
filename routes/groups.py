from collections import defaultdict

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from activity_log import log_member_joined, paginate_activity
from group_permissions import (
    PERM_ADD_EXPENSE,
    PERM_ARCHIVE_GROUP,
    PERM_MANAGE_MEMBERS,
    PERM_MANAGE_SETTINGS,
    PERM_TRANSFER_OWNERSHIP,
    PERM_VIEW_ACTIVITY,
    PERM_VIEW_GROUP,
    PERM_VIEW_SETTLEMENT,
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_OWNER,
    can,
    can_assign_role,
    can_remove_member,
    normalize_role,
)
from models import (
    PAYMENT_STATUS_PAID,
    Expense,
    ExpensePaymentLink,
    Trip,
    TripMember,
    User,
    db,
)
from services.guest_payments import build_guest_payment_url_for_link
from notifications import notify_trip_members_of_join, notify_user_added_to_group
from services.trip_access import (
    generate_invite_code,
    get_trip_members,
    get_user_expenses,
    membership_role_map,
    require_trip_permission,
)
from settle_suggestions import find_pending_payment_link, settle_suggestions_for_trip
from settlemet import calculate_settlement, compute_net_balances


def _link_payment_status(link) -> str:
    if link is None:
        return "suggested"
    if link.status == PAYMENT_STATUS_PAID:
        return "paid"
    return "pending"


def _build_group_settlement_payments(
    trip_id: int,
    settlements: list,
    members: list,
    viewer,
    expenses: list,
) -> tuple[list, list, int]:
    """Presentation rows for simplified group settlements (algorithm unchanged)."""
    members_by_name = {member.name: member for member in members}
    balance_rows = []
    non_zero_count = 0

    net_balances = compute_net_balances(expenses, members) if members and expenses else {}

    for member in members:
        net = float(net_balances.get(member.name, 0))
        if abs(net) > 0.01:
            non_zero_count += 1
        balance_rows.append(
            {
                "name": member.name,
                "net": net,
                "is_you": member.id == viewer.id,
            }
        )

    settlement_payments = []
    for settlement in settlements:
        from_name = settlement["from"]
        to_name = settlement["to"]
        debtor = members_by_name.get(from_name)
        creditor = members_by_name.get(to_name)
        link = None
        if debtor and creditor:
            link = find_pending_payment_link(debtor.id, creditor.id, [trip_id])

        pay_url = None
        if link and (from_name == viewer.name or to_name == viewer.name):
            pay_url = build_guest_payment_url_for_link(link)

        settlement_payments.append(
            {
                "from_name": from_name,
                "to_name": to_name,
                "amount": settlement["amount"],
                "pay_url": pay_url,
                "status": _link_payment_status(link),
            }
        )

    return balance_rows, settlement_payments, non_zero_count


def register(app):
    @app.route("/groups/<int:trip_id>/activity")
    @app.route("/trips/<int:trip_id>/activity")
    @login_required
    def activity_trip(trip_id):
        trip, _membership = require_trip_permission(trip_id, PERM_VIEW_ACTIVITY)
        page = request.args.get("page", 1, type=int)
        pagination = paginate_activity(trip_id=trip.id, page=page, per_page=20)
        return render_template(
            "activity.html",
            pagination=pagination,
            scope="trip",
            trip=trip,
        )

    @app.route("/groups/<int:trip_id>/toggle-active", methods=["POST"])
    @app.route("/trips/<int:trip_id>/toggle-active", methods=["POST"])
    @login_required
    def toggle_group_active(trip_id):
        """Archive or reopen a group (owner only)."""
        trip, _membership = require_trip_permission(trip_id, PERM_ARCHIVE_GROUP)

        description = request.form.get("description", "").strip()
        trip.is_active = not trip.is_active
        if description:
            trip.description = description
        db.session.commit()

        status = "active" if trip.is_active else "inactive"
        flash(f"Trip marked as {status}.", "success")

        referer = request.form.get("redirect_to", "")
        if referer == "trips_page":
            return redirect(url_for("dashboard_groups"))
        return redirect(url_for("group_details", trip_id=trip.id))

    @app.route("/groups/<int:trip_id>/settings", methods=["POST"])
    @app.route("/trips/<int:trip_id>/settings", methods=["POST"])
    @login_required
    def update_group_settings(trip_id):
        """Update group name or description (admin+)."""
        trip, _membership = require_trip_permission(trip_id, PERM_MANAGE_SETTINGS)

        trip_name = request.form.get("trip_name", "").strip()
        description = request.form.get("description", "").strip()
        if trip_name:
            trip.trip_name = trip_name
        if description or "description" in request.form:
            trip.description = description or None
        db.session.commit()
        flash("Group settings updated.", "success")
        return redirect(url_for("group_details", trip_id=trip.id))

    @app.route("/groups/<int:trip_id>/members/<int:user_id>/role", methods=["POST"])
    @app.route("/trips/<int:trip_id>/members/<int:user_id>/role", methods=["POST"])
    @login_required
    def change_member_role(trip_id, user_id):
        trip, actor_membership = require_trip_permission(trip_id, PERM_MANAGE_MEMBERS)

        new_role = normalize_role(request.form.get("role", "").strip().lower())
        if not can_assign_role(actor_membership.role, new_role):
            abort(403)

        target = TripMember.query.filter_by(trip_id=trip.id, user_id=user_id).first()
        if target is None:
            abort(404)
        if normalize_role(target.role) == ROLE_OWNER:
            abort(403)

        target.role = new_role
        db.session.commit()
        flash("Member role updated.", "success")
        return redirect(url_for("group_details", trip_id=trip.id))

    @app.route("/groups/<int:trip_id>/members/<int:user_id>/remove", methods=["POST"])
    @app.route("/trips/<int:trip_id>/members/<int:user_id>/remove", methods=["POST"])
    @login_required
    def remove_group_member(trip_id, user_id):
        trip, actor_membership = require_trip_permission(trip_id, PERM_MANAGE_MEMBERS)

        target = TripMember.query.filter_by(trip_id=trip.id, user_id=user_id).first()
        if target is None:
            abort(404)
        if not can_remove_member(actor_membership.role, target.role):
            abort(403)

        db.session.delete(target)
        db.session.commit()
        flash("Member removed from group.", "success")
        return redirect(url_for("group_details", trip_id=trip.id))

    @app.route("/groups/<int:trip_id>/transfer-ownership", methods=["POST"])
    @app.route("/trips/<int:trip_id>/transfer-ownership", methods=["POST"])
    @login_required
    def transfer_group_ownership(trip_id):
        trip, owner_membership = require_trip_permission(trip_id, PERM_TRANSFER_OWNERSHIP)

        try:
            new_owner_id = int(request.form.get("new_owner_user_id", ""))
        except (TypeError, ValueError):
            abort(400)

        if new_owner_id == current_user.id:
            abort(400)

        new_owner_membership = TripMember.query.filter_by(
            trip_id=trip.id,
            user_id=new_owner_id,
        ).first()
        if new_owner_membership is None:
            abort(404)

        owner_membership.role = ROLE_ADMIN
        new_owner_membership.role = ROLE_OWNER
        trip.created_by = new_owner_id
        db.session.commit()
        flash("Ownership transferred.", "success")
        return redirect(url_for("group_details", trip_id=trip.id))

    @app.route("/groups/create", methods=["GET", "POST"])
    @app.route("/trips/create", methods=["GET", "POST"])
    @login_required
    def create_group():
        if request.method == "POST":
            trip_name = request.form.get("trip_name", "").strip()
            if not trip_name:
                flash("Group name is required.", "error")
                return redirect(url_for("create_group"))

            trip = Trip(
                trip_name=trip_name,
                invite_code=generate_invite_code(),
                created_by=current_user.id,
            )
            db.session.add(trip)
            db.session.flush()
            db.session.add(
                TripMember(
                    trip_id=trip.id,
                    user_id=current_user.id,
                    role=ROLE_OWNER,
                )
            )
            log_member_joined(trip.id, current_user.id, trip.trip_name)
            notify_user_added_to_group(current_user.id, trip.id, trip.trip_name)
            notify_trip_members_of_join(trip.id, current_user.id, trip.trip_name)
            db.session.commit()

            flash("Group created successfully.", "success")
            return redirect(url_for("new_split", group_id=trip.id))

        trips, expenses = get_user_expenses()
        totals_by_trip = defaultdict(float)
        counts_by_trip = defaultdict(int)

        for expense in expenses:
            if expense.trip_id is None:
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
        ]

        return render_template("create_trip.html", trip_summaries=trip_summaries)

    @app.route("/groups/join", methods=["POST"])
    @app.route("/trips/join", methods=["POST"])
    @login_required
    def join_group():
        invite_code = request.form.get("invite_code", "").strip().upper()
        trip = Trip.query.filter_by(invite_code=invite_code).first()

        if not trip:
            flash("No group found with that invite code.", "error")
            return redirect(url_for("create_group"))

        existing_member = TripMember.query.filter_by(
            trip_id=trip.id,
            user_id=current_user.id,
        ).first()
        if existing_member:
            flash("You are already in this group.", "success")
            return redirect(url_for("group_details", trip_id=trip.id))

        db.session.add(
            TripMember(
                trip_id=trip.id,
                user_id=current_user.id,
                role=ROLE_MEMBER,
            )
        )
        log_member_joined(trip.id, current_user.id, trip.trip_name)
        notify_user_added_to_group(current_user.id, trip.id, trip.trip_name)
        notify_trip_members_of_join(trip.id, current_user.id, trip.trip_name)
        db.session.commit()
        flash("Joined group successfully.", "success")
        return redirect(url_for("group_details", trip_id=trip.id))

    @app.route("/groups/<int:trip_id>")
    @app.route("/trips/<int:trip_id>")
    @login_required
    def group_details(trip_id):
        trip, membership = require_trip_permission(trip_id, PERM_VIEW_GROUP)

        memberships = TripMember.query.filter_by(trip_id=trip.id).all()
        roles_by_user = membership_role_map(trip.id)
        member_ids = [row.user_id for row in memberships]
        members = User.query.filter(User.id.in_(member_ids)).order_by(User.name).all()
        expenses = (
            Expense.query.filter_by(trip_id=trip.id).order_by(Expense.created_at.desc()).all()
        )
        total = round(sum(expense.amount or 0 for expense in expenses), 2)

        member_spending = {}
        for member in members:
            member_spending[member.id] = round(
                sum(e.amount or 0 for e in expenses if e.paid_by == member.id), 2
            )

        settlements = calculate_settlement(expenses, members) if members and expenses else []

        member_settlement = {}
        for member in members:
            net = 0
            for s in settlements:
                if s["from"] == member.name:
                    net -= s["amount"]
                elif s["to"] == member.name:
                    net += s["amount"]
            member_settlement[member.id] = round(net, 2)

        expense_pay_links = defaultdict(list)
        expense_ids = [expense.id for expense in expenses]
        if expense_ids:
            payment_links = ExpensePaymentLink.query.filter(
                ExpensePaymentLink.expense_id.in_(expense_ids)
            ).all()
            members_by_id = {member.id: member for member in members}
            for link in payment_links:
                guest = members_by_id.get(link.user_id) or link.user
                expense_pay_links[link.expense_id].append(
                    {
                        "guest_name": guest.name if guest else "Guest",
                        "amount": link.amount_owed,
                        "status": link.status,
                        "url": build_guest_payment_url_for_link(link),
                    }
                )

        actor_role = membership.role
        member_editable = {}
        member_removable = {}
        for member in members:
            target_role = roles_by_user.get(member.id, ROLE_MEMBER)
            if member.id == current_user.id or normalize_role(target_role) == ROLE_OWNER:
                member_editable[member.id] = False
                member_removable[member.id] = False
            else:
                member_editable[member.id] = (
                    can(actor_role, PERM_MANAGE_MEMBERS)
                    and can_assign_role(actor_role, ROLE_MEMBER)
                )
                member_removable[member.id] = (
                    can(actor_role, PERM_MANAGE_MEMBERS)
                    and can_remove_member(actor_role, target_role)
                )

        return render_template(
            "trip_details.html",
            trip=trip,
            members=members,
            member_roles=roles_by_user,
            current_membership=membership,
            can_add_expense=can(membership.role, PERM_ADD_EXPENSE) and trip.is_active,
            can_archive_group=can(membership.role, PERM_ARCHIVE_GROUP),
            can_manage_settings=can(membership.role, PERM_MANAGE_SETTINGS),
            can_manage_members=can(membership.role, PERM_MANAGE_MEMBERS),
            can_transfer_ownership=can(membership.role, PERM_TRANSFER_OWNERSHIP),
            member_editable=member_editable,
            member_removable=member_removable,
            expenses=expenses,
            recent_expenses=expenses[:8],
            expense_count=len(expenses),
            total=total,
            member_spending=member_spending,
            settlements=settlements,
            member_settlement=member_settlement,
            expense_pay_links=expense_pay_links,
            open_new_expense=request.args.get("new") == "1",
            expense_form_action=url_for("add_expense", trip_id=trip.id),
            scan_receipt_url=url_for("scan_receipt", trip_id=trip.id),
        )

    @app.route("/groups/<int:trip_id>/settlement")
    @app.route("/trips/<int:trip_id>/settlement")
    @login_required
    def settlement(trip_id):
        trip, _membership = require_trip_permission(trip_id, PERM_VIEW_SETTLEMENT)

        memberships = TripMember.query.filter_by(trip_id=trip.id).all()
        member_ids = [membership.user_id for membership in memberships]
        members = User.query.filter(User.id.in_(member_ids)).order_by(User.name).all()
        expenses = Expense.query.filter_by(trip_id=trip.id).all()
        settlements = calculate_settlement(expenses, members) if members else []
        settle_suggestions = settle_suggestions_for_trip(
            trip.id, members, viewer_id=current_user.id
        )
        balance_rows, settlement_payments, non_zero_count = _build_group_settlement_payments(
            trip.id,
            settlements,
            members,
            current_user,
            expenses,
        )

        return render_template(
            "settlement.html",
            trip=trip,
            balance_rows=balance_rows,
            settlement_payments=settlement_payments,
            non_zero_count=non_zero_count,
            settle_suggestions=settle_suggestions,
        )
