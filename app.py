import random
import string
import os
import io
import csv
from collections import defaultdict

from sqlalchemy import inspect, text

from flask import Flask, flash, redirect, render_template, request, url_for, jsonify, session, Response
from flask_bcrypt import Bcrypt
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)

from config import Config
from models import Expense, Trip, TripMember, User, db
from settlemet import calculate_settlement


app = Flask(__name__, static_folder="style", static_url_path="/static")
app.config.from_object(Config)

db.init_app(app)
bcrypt = Bcrypt(app)

# Register zip as a Jinja2 filter so templates can use label|zip(values)
app.jinja_env.filters['zip'] = zip


login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "error"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def generate_invite_code():
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not Trip.query.filter_by(invite_code=code).first():
            return code


def get_user_trips():
    memberships = TripMember.query.filter_by(user_id=current_user.id).all()
    trip_ids = [membership.trip_id for membership in memberships]
    if not trip_ids:
        return []
    return Trip.query.filter(Trip.id.in_(trip_ids)).order_by(Trip.created_at.desc()).all()


def get_user_expenses():
    trips = get_user_trips()
    trip_ids = [trip.id for trip in trips]
    if not trip_ids:
        return trips, []

    expenses = (
        Expense.query.filter(Expense.trip_id.in_(trip_ids))
        .order_by(Expense.created_at.desc())
        .all()
    )
    return trips, expenses


def get_trip_or_redirect(trip_id):
    trip = Trip.query.get_or_404(trip_id)
    member = TripMember.query.filter_by(
        trip_id=trip.id,
        user_id=current_user.id,
    ).first()
    if not member:
        flash("You are not a member of that trip.", "error")
        return None
    return trip


def get_all_friends():
    """Get all unique friends across all trips for the current user."""
    trips = get_user_trips()
    friend_ids = set()
    for trip in trips:
        members = TripMember.query.filter_by(trip_id=trip.id).all()
        for m in members:
            if m.user_id != current_user.id:
                friend_ids.add(m.user_id)
    if not friend_ids:
        return []
    return User.query.filter(User.id.in_(friend_ids)).order_by(User.name).all()


def get_global_settlements():
    """Calculate net settlements across all trips for current user."""
    trips = get_user_trips()
    # net_balance[friend_name] = amount (positive = they owe you, negative = you owe them)
    net_balance = defaultdict(float)

    for trip in trips:
        memberships = TripMember.query.filter_by(trip_id=trip.id).all()
        member_ids = [m.user_id for m in memberships]
        members = User.query.filter(User.id.in_(member_ids)).order_by(User.name).all()
        expenses = Expense.query.filter_by(trip_id=trip.id).all()

        if not members or not expenses:
            continue

        settlements = calculate_settlement(expenses, members)
        for s in settlements:
            if s["from"] == current_user.name:
                # current user owes someone
                net_balance[s["to"]] -= s["amount"]
            elif s["to"] == current_user.name:
                # someone owes current user
                net_balance[s["from"]] += s["amount"]

    return dict(net_balance)


def fetch_live_rate(base_currency: str = "INR", target_currency: str = "USD") -> float:
    """Attempt to fetch a live conversion rate from Config.EXCHANGE_API_BASE.

    Returns the conversion multiplier (float) or raises an exception on failure.
    This uses exchangerate.host if set in config and available.
    """
    base = app.config.get("EXCHANGE_API_BASE")
    if not base:
        raise RuntimeError("No exchange API base configured")

    try:
        import importlib
        requests = importlib.import_module("requests")
    except Exception:
        raise RuntimeError("requests library not available")

    # exchangerate.host endpoint example: /latest?base=INR&symbols=USD
    url = f"{base.rstrip('/')}/latest"
    params = {"base": base_currency, "symbols": target_currency}
    resp = requests.get(url, params=params, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    rate = data.get("rates", {}).get(target_currency)
    if rate is None:
        raise RuntimeError("Rate not found in response")
    return float(rate)


@app.route("/fetch_rate", methods=["POST"])
@login_required
def fetch_rate_route():
    # expects form fields base and target (currency codes)
    base = request.form.get("base", "INR").strip().upper()
    target = request.form.get("target", "USD").strip().upper()
    try:
        rate = fetch_live_rate(base, target)
        # store multiplier to convert amounts (base->target)
        session["currency"] = target
        session["conversion_rate"] = rate
        flash(f"Fetched rate: 1 {base} = {rate} {target}", "success")
    except Exception as e:
        flash(f"Failed to fetch rate: {e}", "error")

    return redirect(request.referrer or url_for("dashboard"))


@app.context_processor
def inject_currency():
    # provide a currency symbol/code and conversion_rate to all templates (default from Config)
    default_cur = app.config.get("DEFAULT_CURRENCY", "Rs")
    return {
        "currency": session.get("currency", default_cur),
        "conversion_rate": float(session.get("conversion_rate", 1.0)),
    }


@app.route("/set_currency", methods=["POST"])
@login_required
def set_currency():
    cur = request.form.get("currency", "Rs").strip()
    rate_raw = request.form.get("conversion_rate", "").strip()
    if cur:
        session["currency"] = cur
    # parse conversion rate (multiplier from base amounts to selected currency)
    if rate_raw:
        try:
            rate = float(rate_raw)
            session["conversion_rate"] = rate
        except ValueError:
            flash("Conversion rate must be a number.", "error")

    flash(f"Currency updated to {session.get('currency','Rs')}", "success")
    referer = request.form.get("next") or request.referrer or url_for("dashboard")
    return redirect(referer)


@app.route("/friends/<int:user_id>/export")
@login_required
def friend_export(user_id):
    friend = User.query.get_or_404(user_id)

    # shared trips with current user
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

    # prepare CSV
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
    # Show expenses paid by this friend on trips shared with the current user
    friend = User.query.get_or_404(user_id)

    # Find trips both are members of
    user_trips = {t.id for t in get_user_trips()}
    friend_memberships = TripMember.query.filter_by(user_id=friend.id).all()
    shared_trip_ids = [m.trip_id for m in friend_memberships if m.trip_id in user_trips]

    if not shared_trip_ids:
        shared_expenses = []
        total_spent = 0
    else:
        shared_expenses = (
            Expense.query.filter(Expense.trip_id.in_(shared_trip_ids), Expense.paid_by == friend.id)
            .order_by(Expense.created_at.desc())
            .all()
        )
        total_spent = round(sum(e.amount or 0 for e in shared_expenses), 2)

    # Also compute per-trip breakdown
    per_trip = {}
    for e in shared_expenses:
        per_trip.setdefault(e.trip_id, {"trip_name": None, "total": 0, "count": 0})
        per_trip[e.trip_id]["total"] += e.amount or 0
        per_trip[e.trip_id]["count"] += 1

    # fill trip names
    for trip_id in per_trip.keys():
        trip = Trip.query.get(trip_id)
        per_trip[trip_id]["trip_name"] = trip.trip_name if trip else "Unknown"

    return render_template(
        "friend_detail.html",
        friend=friend,
        shared_expenses=shared_expenses,
        total_spent=total_spent,
        per_trip=per_trip,
    )


@app.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return redirect(url_for("register"))

        user = User(
            name=name,
            email=email,
            password=bcrypt.generate_password_hash(password).decode("utf-8"),
        )
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash("Account created successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user)
            flash("Logged in successfully.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out successfully.", "success")
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required
def dashboard():
    trips, expenses = get_user_expenses()
    total_expenses = sum(expense.amount or 0 for expense in expenses)

    # Per-trip spending for the logged-in user
    user_spend_per_trip = defaultdict(float)
    trip_names = {}
    for trip in trips:
        trip_names[trip.id] = trip.trip_name
        trip_expenses = Expense.query.filter_by(
            trip_id=trip.id, paid_by=current_user.id
        ).all()
        user_spend_per_trip[trip.id] = round(
            sum(e.amount or 0 for e in trip_expenses), 2
        )

    friends = {
        membership.user_id
        for trip in trips
        for membership in TripMember.query.filter_by(trip_id=trip.id).all()
    }

    active_trips = [t for t in trips if t.is_active]
    inactive_trips = [t for t in trips if not t.is_active]

    return render_template(
        "dashboard.html",
        trips=trips,
        active_trips=active_trips,
        inactive_trips=inactive_trips,
        expenses=expenses[:5],
        total_expenses=round(total_expenses, 2),
        friend_count=max(len(friends) - 1, 0),
        user_spend_per_trip=user_spend_per_trip,
        trip_names=trip_names,
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

        # Get members and settlements for this trip
        memberships = TripMember.query.filter_by(trip_id=trip.id).all()
        member_ids = [m.user_id for m in memberships]
        members = User.query.filter(User.id.in_(member_ids)).order_by(User.name).all()
        settlements = calculate_settlement(trip_expenses, members) if members and trip_expenses else []

        # Per-member spending
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

    return render_template(
        "dashboard_expenses.html",
        trip_data=trip_data,
        total_expenses=total_expenses,
    )


@app.route("/dashboard/trips")
@login_required
def dashboard_trips():
    """Active trips management view."""
    trips = get_user_trips()

    trip_data = []
    for trip in trips:
        trip_expenses = Expense.query.filter_by(trip_id=trip.id).all()
        trip_total = round(sum(e.amount or 0 for e in trip_expenses), 2)
        memberships = TripMember.query.filter_by(trip_id=trip.id).all()
        member_count = len(memberships)

        # Current user's spending on this trip
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
        # Find shared trips
        shared_trips = []
        for trip in trips:
            is_member = TripMember.query.filter_by(
                trip_id=trip.id, user_id=friend.id
            ).first()
            if is_member:
                # Get trip-level settlement between current user and this friend
                trip_expenses = Expense.query.filter_by(trip_id=trip.id).all()
                memberships = TripMember.query.filter_by(trip_id=trip.id).all()
                member_ids = [m.user_id for m in memberships]
                members = User.query.filter(User.id.in_(member_ids)).order_by(User.name).all()
                settlements = calculate_settlement(trip_expenses, members) if members and trip_expenses else []

                # Find settlement involving both current user and this friend
                trip_balance = 0
                for s in settlements:
                    if s["from"] == current_user.name and s["to"] == friend.name:
                        trip_balance = -s["amount"]  # Current user owes friend
                    elif s["from"] == friend.name and s["to"] == current_user.name:
                        trip_balance = s["amount"]  # Friend owes current user

                friend_spent_on_trip = round(
                    sum(e.amount or 0 for e in trip_expenses if e.paid_by == friend.id), 2
                )

                shared_trips.append({
                    "trip": trip,
                    "balance": round(trip_balance, 2),
                    "friend_spent": friend_spent_on_trip,
                })

        net_balance = round(net_balances.get(friend.name, 0), 2)

        # total spent across shared trips (sum of friend_spent values)
        total_spent = round(sum(st["friend_spent"] for st in shared_trips), 2)

        friend_data.append({
            "friend": friend,
            "net_balance": net_balance,
            "shared_trips": shared_trips,
            "shared_trip_count": len(shared_trips),
            "total_spent": total_spent,
        })

    # pass a simple raw list of friend names for quick debug/visibility in the template
    raw_friends = [f.name for f in friends]
    return render_template(
        "dashboard_friends.html",
        friend_data=friend_data,
        raw_friends=raw_friends,
        raw_friend_count=len(raw_friends),
    )


@app.route("/trips/<int:trip_id>/toggle-active", methods=["POST"])
@login_required
def toggle_trip_active(trip_id):
    """Toggle trip active/inactive status."""
    trip = get_trip_or_redirect(trip_id)
    if trip is None:
        return redirect(url_for("dashboard"))

    description = request.form.get("description", "").strip()
    trip.is_active = not trip.is_active
    if description:
        trip.description = description
    db.session.commit()

    status = "active" if trip.is_active else "inactive"
    flash(f"Trip marked as {status}.", "success")

    referer = request.form.get("redirect_to", "")
    if referer == "trips_page":
        return redirect(url_for("dashboard_trips"))
    return redirect(url_for("trip_details", trip_id=trip.id))


@app.route("/expenses")
@login_required
def expenses():
    trips, expenses = get_user_expenses()
    trip_names = {trip.id: trip.trip_name for trip in trips}
    totals_by_trip = defaultdict(float)
    counts_by_trip = defaultdict(int)

    for expense in expenses:
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

    total_expenses = round(sum(expense.amount or 0 for expense in expenses), 2)

    # Group expenses by date (newest first)
    expenses_by_date = {}
    date_totals = {}
    for expense in expenses:
        date_key = expense.created_at.strftime("%Y-%m-%d") if expense.created_at else "Unknown"
        if date_key not in expenses_by_date:
            expenses_by_date[date_key] = []
            date_totals[date_key] = 0
        expenses_by_date[date_key].append(expense)
        date_totals[date_key] += expense.amount or 0

    # Round date totals
    date_totals = {k: round(v, 2) for k, v in date_totals.items()}

    return render_template(
        "expenses.html",
        expenses=expenses,
        trip_names=trip_names,
        trip_summaries=trip_summaries,
        total_expenses=total_expenses,
        expenses_by_date=expenses_by_date,
        date_totals=date_totals,
    )



@app.route("/analytics")
@login_required
def analytics():
    trips, expenses = get_user_expenses()
    total_expenses = round(sum(expense.amount or 0 for expense in expenses), 2)
    totals_by_trip = defaultdict(float)

    trip_names = {trip.id: trip.trip_name for trip in trips}
    for expense in expenses:
        totals_by_trip[trip_names.get(expense.trip_id, "Trip")] += expense.amount or 0

    # Friend spending comparison
    friends = get_all_friends()
    friend_spending = {}
    for friend in friends:
        total = 0
        for trip in trips:
            is_member = TripMember.query.filter_by(
                trip_id=trip.id, user_id=friend.id
            ).first()
            if is_member:
                trip_expenses = Expense.query.filter_by(
                    trip_id=trip.id, paid_by=friend.id
                ).all()
                total += sum(e.amount or 0 for e in trip_expenses)
        friend_spending[friend.name] = round(total, 2)

    # Add current user's total spending
    current_user_total = round(
        sum(e.amount or 0 for e in expenses if e.paid_by == current_user.id), 2
    )
    friend_spending[current_user.name + " (You)"] = current_user_total

    # Per-trip user spending for comparison
    user_trip_spending = {}
    for trip in trips:
        user_expenses = Expense.query.filter_by(
            trip_id=trip.id, paid_by=current_user.id
        ).all()
        user_trip_spending[trip.trip_name] = round(
            sum(e.amount or 0 for e in user_expenses), 2
        )

    return render_template(
        "analytics.html",
        total_expenses=total_expenses,
        expense_count=len(expenses),
        trip_labels=list(totals_by_trip.keys()),
        trip_values=[round(amount, 2) for amount in totals_by_trip.values()],
        friend_labels=list(friend_spending.keys()),
        friend_values=list(friend_spending.values()),
        friend_count=len(friends),
        trip_count=len(trips),
        user_trip_labels=list(user_trip_spending.keys()),
        user_trip_values=list(user_trip_spending.values()),
        expenses=expenses,
        trip_names=trip_names,
    )


@app.route("/trips/create", methods=["GET", "POST"])
@login_required
def create_trip():
    if request.method == "POST":
        trip_name = request.form.get("trip_name", "").strip()
        if not trip_name:
            flash("Trip name is required.", "error")
            return redirect(url_for("create_trip"))

        trip = Trip(
            trip_name=trip_name,
            invite_code=generate_invite_code(),
            created_by=current_user.id,
        )
        db.session.add(trip)
        db.session.flush()
        db.session.add(TripMember(trip_id=trip.id, user_id=current_user.id))
        db.session.commit()

        flash("Trip created successfully.", "success")
        return redirect(url_for("trip_details", trip_id=trip.id))

    trips, expenses = get_user_expenses()
    totals_by_trip = defaultdict(float)
    counts_by_trip = defaultdict(int)

    for expense in expenses:
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


@app.route("/trips/join", methods=["POST"])
@login_required
def join_trip():
    invite_code = request.form.get("invite_code", "").strip().upper()
    trip = Trip.query.filter_by(invite_code=invite_code).first()

    if not trip:
        flash("No trip found with that invite code.", "error")
        return redirect(url_for("create_trip"))

    existing_member = TripMember.query.filter_by(
        trip_id=trip.id,
        user_id=current_user.id,
    ).first()
    if existing_member:
        flash("You are already in this trip.", "success")
        return redirect(url_for("trip_details", trip_id=trip.id))

    db.session.add(TripMember(trip_id=trip.id, user_id=current_user.id))
    db.session.commit()
    flash("Joined trip successfully.", "success")
    return redirect(url_for("trip_details", trip_id=trip.id))


@app.route("/trips/<int:trip_id>")
@login_required
def trip_details(trip_id):
    trip = get_trip_or_redirect(trip_id)
    if trip is None:
        return redirect(url_for("dashboard"))

    memberships = TripMember.query.filter_by(trip_id=trip.id).all()
    member_ids = [membership.user_id for membership in memberships]
    members = User.query.filter(User.id.in_(member_ids)).order_by(User.name).all()
    expenses = (
        Expense.query.filter_by(trip_id=trip.id).order_by(Expense.created_at.desc()).all()
    )
    total = round(sum(expense.amount or 0 for expense in expenses), 2)

    totals_by_payer = defaultdict(float)
    for expense in expenses:
        totals_by_payer[expense.payer.name] += expense.amount or 0

    # Per-member spending and settlement
    member_spending = {}
    for member in members:
        member_spending[member.id] = round(
            sum(e.amount or 0 for e in expenses if e.paid_by == member.id), 2
        )

    settlements = calculate_settlement(expenses, members) if members and expenses else []

    # Per-member settlement summary
    member_settlement = {}
    for member in members:
        net = 0
        for s in settlements:
            if s["from"] == member.name:
                net -= s["amount"]
            elif s["to"] == member.name:
                net += s["amount"]
        member_settlement[member.id] = round(net, 2)

    return render_template(
        "trip_details.html",
        trip=trip,
        members=members,
        expenses=expenses,
        total=total,
        chart_labels=list(totals_by_payer.keys()),
        chart_values=[round(amount, 2) for amount in totals_by_payer.values()],
        member_spending=member_spending,
        settlements=settlements,
        member_settlement=member_settlement,
    )


@app.route("/trips/<int:trip_id>/expenses/add", methods=["GET", "POST"])
@login_required
def add_expense(trip_id):
    trip = get_trip_or_redirect(trip_id)
    if trip is None:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        description = request.form.get("description", "").strip()
        amount_raw = request.form.get("amount", "0").strip()

        try:
            amount = float(amount_raw)
        except ValueError:
            amount = 0

        if not description or amount <= 0:
            flash("Enter a description and a valid amount.", "error")
            return redirect(url_for("add_expense", trip_id=trip.id))

        expense = Expense(
            trip_id=trip.id,
            paid_by=current_user.id,
            category="General",
            description=description,
            amount=amount,
        )
        db.session.add(expense)
        db.session.commit()

        flash("Expense added successfully.", "success")
        return redirect(url_for("trip_details", trip_id=trip.id))

    return render_template("add_expense.html", trip=trip)


@app.route("/trips/<int:trip_id>/settlement")
@login_required
def settlement(trip_id):
    trip = get_trip_or_redirect(trip_id)
    if trip is None:
        return redirect(url_for("dashboard"))

    memberships = TripMember.query.filter_by(trip_id=trip.id).all()
    member_ids = [membership.user_id for membership in memberships]
    members = User.query.filter(User.id.in_(member_ids)).order_by(User.name).all()
    expenses = Expense.query.filter_by(trip_id=trip.id).all()
    settlements = calculate_settlement(expenses, members) if members else []

    return render_template(
        "settlement.html",
        trip=trip,
        settlements=settlements,
    )


def init_database():
    with app.app_context():
        db.create_all()
        sync_mysql_schema()


def sync_mysql_schema():
    if db.engine.dialect.name != "mysql":
        return

    inspector = inspect(db.engine)
    schema_updates = {
        "users": {
            "profile_pic": "ALTER TABLE users ADD COLUMN profile_pic VARCHAR(255) DEFAULT 'default.png'",
            "created_at": "ALTER TABLE users ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP",
        },
        "trips": {
            "invite_code": "ALTER TABLE trips ADD COLUMN invite_code VARCHAR(10)",
            "created_by": "ALTER TABLE trips ADD COLUMN created_by INT",
            "created_at": "ALTER TABLE trips ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP",
            "is_active": "ALTER TABLE trips ADD COLUMN is_active BOOLEAN DEFAULT TRUE",
            "description": "ALTER TABLE trips ADD COLUMN description TEXT",
        },
        "trip_members": {
            "trip_id": "ALTER TABLE trip_members ADD COLUMN trip_id INT",
            "user_id": "ALTER TABLE trip_members ADD COLUMN user_id INT",
        },
        "expenses": {
            "trip_id": "ALTER TABLE expenses ADD COLUMN trip_id INT",
            "paid_by": "ALTER TABLE expenses ADD COLUMN paid_by INT",
            "category": "ALTER TABLE expenses ADD COLUMN category VARCHAR(100)",
            "description": "ALTER TABLE expenses ADD COLUMN description VARCHAR(255)",
            "amount": "ALTER TABLE expenses ADD COLUMN amount FLOAT",
            "created_at": "ALTER TABLE expenses ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP",
        },
    }

    with db.engine.begin() as connection:
        for table_name, updates in schema_updates.items():
            if not inspector.has_table(table_name):
                continue

            existing_columns = {
                column["name"]
                for column in inspector.get_columns(table_name)
            }

            for column_name, statement in updates.items():
                if column_name not in existing_columns:
                    connection.execute(text(statement))


if __name__ == "__main__":
    init_database()
    app.run(
        debug=True,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", 5002)),
    )
