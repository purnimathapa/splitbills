import random
import string
import os
from collections import defaultdict

from sqlalchemy import inspect, text

from flask import Flask, flash, redirect, render_template, request, url_for
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
    friends = {
        membership.user_id
        for trip in trips
        for membership in TripMember.query.filter_by(trip_id=trip.id).all()
    }

    return render_template(
        "dashboard.html",
        trips=trips,
        expenses=expenses[:5],
        total_expenses=round(total_expenses, 2),
        friend_count=max(len(friends) - 1, 0),
    )


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
    totals_by_category = defaultdict(float)
    totals_by_trip = defaultdict(float)

    for expense in expenses:
        totals_by_category[expense.category or "General"] += expense.amount or 0

    trip_names = {trip.id: trip.trip_name for trip in trips}
    for expense in expenses:
        totals_by_trip[trip_names.get(expense.trip_id, "Trip")] += expense.amount or 0

    return render_template(
        "analytics.html",
        total_expenses=total_expenses,
        expense_count=len(expenses),
        category_labels=list(totals_by_category.keys()),
        category_values=[round(amount, 2) for amount in totals_by_category.values()],
        trip_labels=list(totals_by_trip.keys()),
        trip_values=[round(amount, 2) for amount in totals_by_trip.values()],
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

    return render_template(
        "trip_details.html",
        trip=trip,
        members=members,
        expenses=expenses,
        total=total,
        chart_labels=list(totals_by_payer.keys()),
        chart_values=[round(amount, 2) for amount in totals_by_payer.values()],
    )


@app.route("/trips/<int:trip_id>/expenses/add", methods=["GET", "POST"])
@login_required
def add_expense(trip_id):
    trip = get_trip_or_redirect(trip_id)
    if trip is None:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "General").strip() or "General"
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
            category=category,
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
        port=int(os.environ.get("PORT", 5001)),
    )
