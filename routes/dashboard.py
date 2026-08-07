import csv
import io
from datetime import datetime

from flask import (
    Response,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from sqlalchemy import or_

from activity_log import paginate_activity, paginate_activity_for_user, recent_activity_for_user
from services.analytics import build_user_analytics
from expense_participants import get_expense_members
from models import Expense, Trip, TripMember, User, db
from services.balances import (
    build_expense_summaries,
    get_all_friends,
    get_global_settlements,
    get_payment_hub_for_user,
)
from services.trip_access import get_user_expenses, get_user_trips
from services.user_messages import GENERIC_ERROR
from settle_suggestions import settle_suggestion_for_friend
from settlemet import calculate_settlement
from standalone_balances import (
    compute_pairwise_net_standalone,
    standalone_expenses_between_users,
    standalone_expenses_for_user,
)
from utils.currency import fetch_live_rate


def register(app):
    @app.route("/friends/<int:user_id>/export")
    @login_required
    def friend_export(user_id):
        friend = User.query.get_or_404(user_id)

        user_trips = {t.id for t in get_user_trips()}
        friend_memberships = TripMember.query.filter_by(user_id=friend.id).all()
        shared_trip_ids = [m.trip_id for m in friend_memberships if m.trip_id in user_trips]

        shared_expenses = []
        if shared_trip_ids:
            shared_expenses = (
                Expense.query.filter(Expense.trip_id.in_(shared_trip_ids), Expense.paid_by == friend.id)
                .order_by(Expense.created_at.desc())
                .all()
            )

        si = io.StringIO()
        writer = csv.writer(si)
        writer.writerow(["date", "trip_name", "description", "amount_base"])
        for e in shared_expenses:
            trip = Trip.query.get(e.trip_id)
            writer.writerow([e.created_at.strftime("%Y-%m-%d") if e.created_at else "", trip.trip_name if trip else "", e.description or "", e.amount or 0])

        output = si.getvalue()
        headers = {
            "Content-Disposition": f"attachment; filename=friend_{friend.id}_expenses.csv",
            "Content-Type": "text/csv",
        }
        return Response(output, headers=headers)

    @app.route("/friends/<int:user_id>")
    @login_required
    def friend_detail(user_id):
        friend = User.query.get_or_404(user_id)

        user_trips = {t.id for t in get_user_trips()}
        friend_memberships = TripMember.query.filter_by(user_id=friend.id).all()
        shared_trip_ids = [m.trip_id for m in friend_memberships if m.trip_id in user_trips]

        shared_expenses = []
        total_spent = 0.0
        if shared_trip_ids:
            shared_expenses = (
                Expense.query.filter(Expense.trip_id.in_(shared_trip_ids), Expense.paid_by == friend.id)
                .order_by(Expense.created_at.desc())
                .all()
            )
            total_spent = round(sum(e.amount or 0 for e in shared_expenses), 2)

        standalone_paid = [
            e
            for e in standalone_expenses_between_users(current_user.id, friend.id)
            if e.paid_by == friend.id
        ]
        if standalone_paid:
            shared_expenses = sorted(
                shared_expenses + standalone_paid,
                key=lambda e: e.created_at or datetime.min,
                reverse=True,
            )
            total_spent = round(
                sum(e.amount or 0 for e in shared_expenses if e.paid_by == friend.id),
                2,
            )

        per_trip = {}
        for e in shared_expenses:
            key = e.trip_id
            per_trip.setdefault(key, {"trip_name": None, "total": 0, "count": 0})
            per_trip[key]["total"] += e.amount or 0
            per_trip[key]["count"] += 1

        for trip_id in list(per_trip.keys()):
            if trip_id is None:
                per_trip[trip_id]["trip_name"] = "One-off splits"
            else:
                trip = Trip.query.get(trip_id)
                per_trip[trip_id]["trip_name"] = trip.trip_name if trip else "Unknown"

        settle_suggestion = settle_suggestion_for_friend(
            friend, shared_trip_ids, viewer_id=current_user.id
        )

        return render_template(
            "friend_detail.html",
            friend=friend,
            shared_expenses=shared_expenses,
            total_spent=total_spent,
            per_trip=per_trip,
            settle_suggestion=settle_suggestion,
        )

    @app.route("/activity")
    @login_required
    def activity_global():
        trips, _ = get_user_expenses()
        trip_ids = [t.id for t in trips]
        page = request.args.get("page", 1, type=int)
        pagination = paginate_activity_for_user(
            current_user.id, trip_ids, page=page, per_page=20
        )
        return render_template(
            "activity.html",
            pagination=pagination,
            scope="global",
            trip=None,
        )

    @app.route("/dashboard")
    @login_required
    def dashboard():
        trips, all_expenses = get_user_expenses()
        trip_ids = [t.id for t in trips]
        trip_names = {t.id: t.trip_name for t in trips}
        friends = get_all_friends()

        analytics = build_user_analytics(
            current_user.id,
            "30",
            trip_count=len([t for t in trips if t.is_active]),
            friend_count=len(friends),
        )

        payment_hub = get_payment_hub_for_user(current_user.id)
        settlements = get_global_settlements()
        total_you_owe = round(
            sum(-amount for amount in settlements.values() if amount < -0.01),
            2,
        )
        total_owed_to_you = round(
            sum(amount for amount in settlements.values() if amount > 0.01),
            2,
        )

        settlement_items = [
            {"name": name, "amount": amount}
            for name, amount in settlements.items()
            if abs(amount) > 0.01
        ]
        settlement_items.sort(key=lambda row: abs(row["amount"]), reverse=True)

        recent_expenses = all_expenses[:8]
        expense_summaries = build_expense_summaries(recent_expenses, current_user.id)
        activity_items = recent_activity_for_user(current_user.id, trip_ids, limit=6)

        group_items = []
        for trip in trips:
            if not trip.is_active:
                continue
            group_items.append(
                {
                    "trip": trip,
                    "expense_count": Expense.query.filter_by(trip_id=trip.id).count(),
                }
            )
        group_items = group_items[:6]

        active_trip_ids = [t.id for t in trips if t.is_active]
        recurring_clauses = [Expense.paid_by == current_user.id]
        if active_trip_ids:
            recurring_clauses.append(Expense.trip_id.in_(active_trip_ids))
        recurring_templates = (
            Expense.query.filter(
                Expense.is_recurring.is_(True),
                Expense.next_occurrence_date.isnot(None),
                or_(*recurring_clauses),
            )
            .order_by(Expense.next_occurrence_date.asc())
            .limit(5)
            .all()
        )

        return render_template(
            "dashboard.html",
            analytics=analytics,
            total_you_owe=total_you_owe,
            total_owed_to_you=total_owed_to_you,
            total_spending=float(analytics.total_spending),
            group_spending=float(analytics.group_spending),
            personal_spending=float(analytics.personal_spending),
            payment_hub=payment_hub,
            settlement_items=settlement_items[:8],
            recent_expenses=recent_expenses,
            expense_summaries=expense_summaries,
            trip_names=trip_names,
            activity_items=activity_items,
            group_items=group_items,
            recurring_templates=recurring_templates,
        )

    @app.route("/dashboard/expenses")
    @login_required
    def dashboard_expenses():
        """Total Expenses detail view — expenses grouped by trip with settlements."""
        trips, expenses = get_user_expenses()
        total_expenses = round(sum(e.amount or 0 for e in expenses), 2)

        trip_data = []
        for trip in trips:
            trip_expenses = Expense.query.filter_by(trip_id=trip.id).order_by(
                Expense.created_at.desc()
            ).all()
            trip_total = round(sum(e.amount or 0 for e in trip_expenses), 2)

            memberships = TripMember.query.filter_by(trip_id=trip.id).all()
            member_ids = [m.user_id for m in memberships]
            members = User.query.filter(User.id.in_(member_ids)).order_by(User.name).all()
            settlements = calculate_settlement(trip_expenses, members) if members and trip_expenses else []

            member_spending = {}
            for member in members:
                member_spending[member.name] = round(
                    sum(e.amount or 0 for e in trip_expenses if e.paid_by == member.id), 2
                )

            trip_data.append({
                "trip": trip,
                "expenses": trip_expenses,
                "total": trip_total,
                "members": members,
                "settlements": settlements,
                "member_spending": member_spending,
            })

        standalone_expenses = standalone_expenses_for_user(current_user.id)
        standalone_data = None
        if standalone_expenses:
            standalone_settlements: list[dict] = []
            for expense in standalone_expenses:
                members = get_expense_members(expense)
                if len(members) < 2:
                    continue
                standalone_settlements.extend(
                    calculate_settlement([expense], members)
                )
            standalone_data = {
                "expenses": standalone_expenses,
                "total": round(
                    sum(e.amount or 0 for e in standalone_expenses),
                    2,
                ),
                "settlements": standalone_settlements,
            }

        return render_template(
            "dashboard_expenses.html",
            trip_data=trip_data,
            standalone_data=standalone_data,
            total_expenses=total_expenses,
        )

    @app.route("/collect")
    @login_required
    def collect():
        """Legacy URL — home shows need-to-collect list."""
        highlight = request.args.get("highlight", type=int)
        url = url_for("dashboard", _anchor="need-collect")
        if highlight:
            url = url_for("dashboard", _anchor="need-collect")
        return redirect(url)

    @app.route("/wallet")
    @login_required
    def wallet():
        """Track collected guest payments (Khalti + manual)."""
        hub = get_payment_hub_for_user(current_user.id)
        return render_template(
            "wallet.html",
            collected_links=hub["collected_links"],
            collected_total=hub["collected_total"],
            pending_total=hub["pending_total"],
        )

    @app.route("/receipts")
    @login_required
    def receipts():
        """Receipt photos and scan entry points."""
        trips = get_user_trips()
        trip_ids = [t.id for t in trips]
        items = []
        if trip_ids:
            items = (
                Expense.query.filter(
                    Expense.trip_id.in_(trip_ids),
                    Expense.receipt_image_url.isnot(None),
                )
                .order_by(Expense.created_at.desc())
                .all()
            )
        trips_by_id = {t.id: t for t in trips}
        first_active = next((t for t in trips if t.is_active), trips[0] if trips else None)
        return render_template(
            "receipts.html",
            receipt_expenses=items,
            trips_by_id=trips_by_id,
            first_active=first_active,
        )

    @app.route("/dashboard/groups")
    @app.route("/dashboard/trips")
    @login_required
    def dashboard_groups():
        """Active groups management view."""
        trips = get_user_trips()

        trip_data = []
        for trip in trips:
            trip_expenses = Expense.query.filter_by(trip_id=trip.id).all()
            trip_total = round(sum(e.amount or 0 for e in trip_expenses), 2)
            memberships = TripMember.query.filter_by(trip_id=trip.id).all()
            member_count = len(memberships)

            user_spent = round(
                sum(e.amount or 0 for e in trip_expenses if e.paid_by == current_user.id), 2
            )

            trip_data.append({
                "trip": trip,
                "total": trip_total,
                "member_count": member_count,
                "expense_count": len(trip_expenses),
                "user_spent": user_spent,
            })

        active_trips = [t for t in trip_data if t["trip"].is_active]
        inactive_trips = [t for t in trip_data if not t["trip"].is_active]

        return render_template(
            "dashboard_trips.html",
            active_trips=active_trips,
            inactive_trips=inactive_trips,
        )

    @app.route("/dashboard/friends")
    @login_required
    def dashboard_friends():
        """Friends detail view with who-owes-whom balances."""
        friends = get_all_friends()
        trips = get_user_trips()
        net_balances = get_global_settlements()

        friend_data = []
        for friend in friends:
            shared_trips = []
            for trip in trips:
                is_member = TripMember.query.filter_by(
                    trip_id=trip.id, user_id=friend.id
                ).first()
                if is_member:
                    trip_expenses = Expense.query.filter_by(trip_id=trip.id).all()
                    memberships = TripMember.query.filter_by(trip_id=trip.id).all()
                    member_ids = [m.user_id for m in memberships]
                    members = User.query.filter(User.id.in_(member_ids)).order_by(User.name).all()
                    settlements = calculate_settlement(trip_expenses, members) if members and trip_expenses else []

                    trip_balance = 0
                    for s in settlements:
                        if s["from"] == current_user.name and s["to"] == friend.name:
                            trip_balance = -s["amount"]
                        elif s["from"] == friend.name and s["to"] == current_user.name:
                            trip_balance = s["amount"]

                    friend_spent_on_trip = round(
                        sum(e.amount or 0 for e in trip_expenses if e.paid_by == friend.id), 2
                    )

                    shared_trips.append({
                        "trip": trip,
                        "balance": round(trip_balance, 2),
                        "friend_spent": friend_spent_on_trip,
                    })

            one_off = standalone_expenses_between_users(current_user.id, friend.id)
            if one_off:
                one_off_balance = compute_pairwise_net_standalone(
                    current_user.id, friend.id
                )
                one_off_spent = round(
                    sum(e.amount or 0 for e in one_off if e.paid_by == friend.id),
                    2,
                )
                shared_trips.append(
                    {
                        "trip": None,
                        "trip_label": "One-off splits",
                        "balance": one_off_balance,
                        "friend_spent": one_off_spent,
                    }
                )

            net_balance = round(net_balances.get(friend.name, 0), 2)
            total_spent = round(sum(st["friend_spent"] for st in shared_trips), 2)

            friend_data.append({
                "friend": friend,
                "net_balance": net_balance,
                "shared_trips": shared_trips,
                "shared_trip_count": len(shared_trips),
                "total_spent": total_spent,
            })

        raw_friends = [f.name for f in friends]
        return render_template(
            "dashboard_friends.html",
            friend_data=friend_data,
            raw_friends=raw_friends,
            raw_friend_count=len(raw_friends),
        )

    @app.route("/fetch_rate", methods=["POST"])
    @login_required
    def fetch_rate_route():
        base = request.form.get("base", "INR").strip().upper()
        target = request.form.get("target", "USD").strip().upper()
        try:
            rate = fetch_live_rate(base, target)
            session["currency"] = target
            session["conversion_rate"] = rate
            flash(f"Exchange rate updated ({target}).", "success")
        except Exception:
            flash(GENERIC_ERROR, "error")

        return redirect(request.referrer or url_for("dashboard"))
