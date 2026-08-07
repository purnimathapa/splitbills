from flask import render_template, request
from flask_login import current_user, login_required

from services.analytics import build_user_analytics, parse_range_key
from services.balances import get_all_friends
from services.trip_access import get_user_trips


def register(app):
    @app.route("/analytics")
    @login_required
    def analytics():
        range_key = parse_range_key(request.args.get("range"))
        trips = get_user_trips()
        friends = get_all_friends()
        stats = build_user_analytics(
            current_user.id,
            range_key,
            trip_count=len(trips),
            friend_count=len(friends),
        )

        return render_template(
            "analytics.html",
            range_key=stats.range_key,
            total_expenses=float(stats.total_spending),
            expense_count=stats.expense_count,
            group_spending=float(stats.group_spending),
            personal_spending=float(stats.personal_spending),
            amount_owed=float(stats.settlement.amount_owed),
            amount_receivable=float(stats.settlement.amount_receivable),
            settlement_pending_count=stats.settlement.pending_count,
            settlement_paid_count=stats.settlement.paid_count,
            settlement_pending_total=float(stats.settlement.pending_total),
            settlement_collected_total=float(stats.settlement.collected_total),
            recurring_active_count=stats.recurring.active_count,
            recurring_monthly_count=stats.recurring.monthly_count,
            recurring_weekly_count=stats.recurring.weekly_count,
            recurring_per_occurrence_total=float(stats.recurring.per_occurrence_total),
            category_labels=stats.category_labels,
            category_values=stats.category_values,
            trip_labels=stats.trip_labels,
            trip_values=stats.trip_values,
            trend_labels=stats.trend_labels,
            trend_values=stats.trend_values,
            friend_count=stats.friend_count,
            trip_count=stats.trip_count,
        )
