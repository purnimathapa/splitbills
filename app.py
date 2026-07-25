import random
import string
import os
import io
import csv
from collections import defaultdict
from datetime import datetime

from sqlalchemy import inspect, or_, text

from flask import Flask, flash, redirect, render_template, request, url_for, jsonify, session, Response
from flask_bcrypt import Bcrypt
from flask_mail import Mail
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)

from config import Config
from models import (
    Expense,
    ExpenseItem,
    ExpenseItemAssignment,
    ExpenseParticipant,
    ExpensePaymentLink,
    ExpenseSplit,
    Trip,
    TripMember,
    User,
    db,
    NOTIFICATION_PAYMENT_RECEIVED,
    NOTIFICATION_REMINDER_SENT,
    NOTIFICATION_TRIP_JOIN,
    PAYMENT_STATUS_PAID,
    PAYMENT_STATUS_PENDING,
    SPLIT_TYPE_EQUAL,
    SPLIT_TYPE_EXACT,
    SPLIT_TYPE_ITEMIZED,
    SPLIT_TYPE_PERCENTAGE,
    SPLIT_TYPE_SHARES,
)
from expense_split_logic import (
    compute_equal_split,
    compute_exact_split,
    compute_itemized_split,
    compute_percentage_split,
    compute_shares_split,
    parse_itemized_form,
    parse_member_amount_fields,
    round_money,
)
from khalti_pay import (
    confirm_khalti_for_payment_link,
    initiate_khalti_payment,
    khalti_configured,
    khalti_purchase_order_id,
    parse_purchase_order_link_id,
)
from stripe_pay import (
    create_checkout_session,
    retrieve_checkout_session,
    stripe_configured,
)
from payment_links import (
    build_guest_claim_url,
    build_guest_payment_url,
    create_expense_payment_links,
    resolve_payment_link,
)
from item_claims import (
    claim_status_for_expense,
    create_self_service_payment_links,
    finalize_expense_claims,
    maybe_auto_finalize,
    parse_itemized_line_items_only,
    preview_user_total,
    save_user_claims,
    assignments_by_item_id,
)
from expense_create import create_expense_from_form, expense_detail_url
from expense_participants import (
    get_expense_member_ids,
    get_expense_members,
    parse_participant_ids_from_form,
    user_can_access_expense,
)
from receipt_upload import save_receipt_file, validate_receipt_file
from receipt_ocr import scan_receipt_image
from scheduler_setup import init_scheduler_for_app
from settlemet import calculate_settlement
from recurring_expenses import parse_recurrence_from_form
from activity_log import (
    ACTIVITY_ICONS,
    log_expense_created,
    log_member_joined,
    log_payment_confirmed,
    paginate_activity,
    paginate_activity_for_user,
    recent_activity,
    recent_activity_for_user,
)
from settle_suggestions import (
    build_pairwise_suggestion_raw,
    find_pending_payment_link,
)
from standalone_balances import (
    apply_standalone_to_pairwise_balances,
    friend_user_ids_from_standalone,
    net_balance_from_standalone,
    standalone_expenses_between_users,
    standalone_expenses_for_user,
    compute_pairwise_net_standalone,
)
from analytics_data import (
    aggregate_category_spending,
    aggregate_spending_trend,
    aggregate_trip_spending,
    filter_expenses_by_range,
    parse_range_key,
)
from notifications import (
    create_notification,
    mark_all_read,
    recent_notifications,
    unread_count,
)


app = Flask(__name__, static_folder="style", static_url_path="/static")
app.config.from_object(Config)

db.init_app(app)
bcrypt = Bcrypt(app)
mail = Mail(app)

# Register zip as a Jinja2 filter so templates can use label|zip(values)
app.jinja_env.filters['zip'] = zip

AVATAR_PALETTE = [
    '#2563eb', '#0891b2', '#059669', '#7c3aed',
    '#db2777', '#ea580c', '#4f46e5', '#0d9488',
]


@app.template_filter('avatar_color')
def avatar_color_filter(name):
    """Deterministic avatar background from display name."""
    text = (name or '?').strip() or '?'
    h = 0
    for char in text:
        h = (h * 31 + ord(char)) & 0xFFFFFFFF
    return AVATAR_PALETTE[h % len(AVATAR_PALETTE)]


@app.template_filter('time_ago')
def time_ago_filter(value):
    """Human-readable relative time from a UTC datetime."""
    if value is None:
        return ""
    now = datetime.utcnow()
    if hasattr(value, "replace") and getattr(value, "tzinfo", None):
        value = value.replace(tzinfo=None)
    delta = now - value
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 45:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 7:
        return f"{days}d ago"
    weeks = days // 7
    if weeks < 5:
        return f"{weeks}w ago"
    return value.strftime("%d %b %Y")


@app.template_global()
def activity_icon(action_type):
    return ACTIVITY_ICONS.get(action_type, "•")


login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "error"


@app.context_processor
def inject_notification_context():
    if not current_user.is_authenticated:
        return {}
    return {
        "notification_unread_count": unread_count(current_user.id),
        "notification_preview": recent_notifications(current_user.id, 6),
    }


@app.template_global()
def expense_url(expense):
    """Link to expense detail (split group or one-off)."""
    if expense is None:
        return url_for("dashboard")
    if expense.trip_id:
        return url_for(
            "expense_detail",
            trip_id=expense.trip_id,
            expense_id=expense.id,
        )
    return url_for("expense_detail_standalone", expense_id=expense.id)


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

    filters = [Expense.paid_by == current_user.id]
    if trip_ids:
        filters.append(Expense.trip_id.in_(trip_ids))

    participant_rows = (
        db.session.query(ExpenseParticipant.expense_id)
        .filter(ExpenseParticipant.user_id == current_user.id)
        .all()
    )
    participant_expense_ids = [row[0] for row in participant_rows]
    if participant_expense_ids:
        filters.append(Expense.id.in_(participant_expense_ids))

    expenses = (
        Expense.query.filter(or_(*filters))
        .order_by(Expense.created_at.desc())
        .all()
    )
    return trips, expenses


def get_trip_members(trip_id):
    memberships = TripMember.query.filter_by(trip_id=trip_id).all()
    member_ids = [membership.user_id for membership in memberships]
    if not member_ids:
        return []
    return User.query.filter(User.id.in_(member_ids)).order_by(User.name).all()


def persist_expense_splits(expense_id: int, owed_by_user: dict[int, float]) -> None:
    for user_id, amount_owed in owed_by_user.items():
        if amount_owed <= 0:
            continue
        db.session.add(
            ExpenseSplit(
                expense_id=expense_id,
                user_id=user_id,
                amount_owed=round_money(amount_owed),
            )
        )


def build_guest_payment_url_for_link(link: ExpensePaymentLink) -> str:
    return build_guest_payment_url(link, app.config["SECRET_KEY"])


def build_guest_claim_url_for_link(link: ExpensePaymentLink) -> str:
    return build_guest_claim_url(link, app.config["SECRET_KEY"])


def get_trip_member_ids(trip_id: int) -> list[int]:
    memberships = TripMember.query.filter_by(trip_id=trip_id).all()
    return [m.user_id for m in memberships]


def get_payment_link_for_token(token: str) -> ExpensePaymentLink | None:
    return resolve_payment_link(
        token,
        app.config["SECRET_KEY"],
        db.session,
        ExpensePaymentLink,
    )


def mark_payment_link_paid(
    link: ExpensePaymentLink,
    provider: str,
    *,
    khalti_pidx: str | None = None,
    commit: bool = True,
) -> bool:
    """Mark guest share settled. Returns False if already paid (idempotent)."""
    if link.status == PAYMENT_STATUS_PAID:
        return False
    link.status = PAYMENT_STATUS_PAID
    link.paid_at = datetime.utcnow()
    link.payment_provider = provider
    if khalti_pidx:
        link.khalti_pidx = khalti_pidx
    log_payment_confirmed(link, provider)
    _notify_payment_received(link)
    if commit:
        db.session.commit()
    return True


def _notify_payment_received(link: ExpensePaymentLink) -> None:
    expense = link.expense
    guest = link.user
    if expense is None or expense.paid_by is None or guest is None:
        return
    payer_id = expense.paid_by
    if payer_id == guest.id:
        return
    href = None
    if expense.trip_id:
        try:
            href = url_for(
                "expense_detail",
                trip_id=expense.trip_id,
                expense_id=expense.id,
            )
        except RuntimeError:
            href = f"/groups/{expense.trip_id}/expenses/{expense.id}"
    create_notification(
        payer_id,
        f"{guest.name} paid {link.amount_owed:.2f} for “{expense.description or 'an expense'}”",
        kind=NOTIFICATION_PAYMENT_RECEIVED,
        href=href,
    )


def notify_trip_members_of_join(trip_id: int, actor_user_id: int, trip_name: str) -> None:
    actor = db.session.get(User, actor_user_id)
    actor_name = actor.name if actor else "Someone"
    group_url = url_for("group_details", trip_id=trip_id)
    memberships = TripMember.query.filter_by(trip_id=trip_id).all()
    for membership in memberships:
        if membership.user_id == actor_user_id:
            continue
        create_notification(
            membership.user_id,
            f"{actor_name} joined {trip_name}",
            kind=NOTIFICATION_TRIP_JOIN,
            href=group_url,
        )


def settle_link_via_khalti_lookup(link: ExpensePaymentLink, pidx: str) -> tuple[bool, str]:
    """Confirm payment with Khalti lookup API, then mark the link paid."""
    if link.status == PAYMENT_STATUS_PAID:
        return True, "already_paid"

    ok, message, _lookup = confirm_khalti_for_payment_link(
        secret_key=app.config["KHALTI_SECRET_KEY"],
        pidx=pidx,
        payment_link_id=link.id,
        amount_owed_rupees=link.amount_owed,
    )
    if not ok:
        return False, message

    mark_payment_link_paid(link, "khalti", khalti_pidx=pidx, commit=True)
    return True, message


def settle_link_via_stripe_session(link: ExpensePaymentLink, session_id: str) -> tuple[bool, str]:
    if link.status == PAYMENT_STATUS_PAID:
        return True, "already_paid"
    if not stripe_configured(app):
        return False, "stripe_not_configured"
    try:
        data = retrieve_checkout_session(app.config["STRIPE_SECRET_KEY"], session_id)
    except Exception as exc:
        return False, f"stripe_lookup_failed:{exc}"

    if data.get("payment_status") != "paid":
        return False, f"not_paid:{data.get('payment_status')}"

    meta_id = data.get("metadata", {}).get("payment_link_id")
    ref_id = data.get("client_reference_id")
    expected = str(link.id)
    if meta_id != expected and ref_id != expected:
        return False, "link_mismatch"

    expected_paisa = int(round(link.amount_owed * 100))
    paid = int(data.get("amount_total") or 0)
    if paid and abs(paid - expected_paisa) > 2:
        return False, f"amount_mismatch:{paid}!={expected_paisa}"

    mark_payment_link_paid(
        link,
        "stripe",
        commit=True,
    )
    link.stripe_checkout_session_id = session_id
    db.session.commit()
    return True, "confirmed"


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


def get_split_candidate_users():
    """People you can add to a one-off receipt split (not tied to a group)."""
    ids: set[int] = {current_user.id}
    ids.update(friend_user_ids_from_standalone(current_user.id))
    for friend in get_all_friends():
        ids.add(friend.id)
    return User.query.filter(User.id.in_(ids)).order_by(User.name).all()


def guest_link_url_for_row(link: ExpensePaymentLink, expense: Expense | None) -> str:
    if expense and getattr(expense, "self_service_items", False) and not expense.claims_finalized_at:
        return build_guest_claim_url_for_link(link)
    return build_guest_payment_url_for_link(link)


def get_payment_hub_for_user(user_id: int) -> dict:
    """Pending/collected guest links for expenses this user paid."""
    paid_expense_ids = [
        row[0]
        for row in db.session.query(Expense.id)
        .filter(Expense.paid_by == user_id)
        .all()
    ]
    if not paid_expense_ids:
        return {
            "pending_links": [],
            "collected_links": [],
            "pending_total": 0.0,
            "collected_total": 0.0,
            "pending_count": 0,
        }

    links = (
        ExpensePaymentLink.query.filter(
            ExpensePaymentLink.expense_id.in_(paid_expense_ids)
        )
        .order_by(ExpensePaymentLink.created_at.desc())
        .all()
    )
    pending = []
    collected = []
    pending_total = 0.0
    collected_total = 0.0
    for link in links:
        expense = link.expense
        guest = link.user
        trip = Trip.query.get(expense.trip_id) if expense else None
        row = {
            "link": link,
            "expense": expense,
            "guest": guest,
            "trip": trip,
            "url": guest_link_url_for_row(link, expense),
            "is_claim_link": bool(
                expense
                and getattr(expense, "self_service_items", False)
                and not expense.claims_finalized_at
            ),
        }
        if link.status == PAYMENT_STATUS_PAID:
            collected.append(row)
            collected_total += link.amount_owed or 0
        else:
            pending.append(row)
            pending_total += link.amount_owed or 0

    return {
        "pending_links": pending,
        "collected_links": collected,
        "pending_total": round(pending_total, 2),
        "collected_total": round(collected_total, 2),
        "pending_count": len(pending),
    }


def get_user_net_balance(user_id: int) -> float:
    """Net settlement balance across all groups (positive = others owe you)."""
    trips = get_user_trips()
    net = 0.0
    for trip in trips:
        expenses = Expense.query.filter_by(trip_id=trip.id).all()
        if not expenses:
            continue
        memberships = TripMember.query.filter_by(trip_id=trip.id).all()
        member_ids = [m.user_id for m in memberships]
        members = User.query.filter(User.id.in_(member_ids)).all()
        user = User.query.get(user_id)
        if not user or user not in members:
            continue
        settlements = calculate_settlement(expenses, members)
        for s in settlements:
            if s["from"] == user.name:
                net -= s["amount"]
            elif s["to"] == user.name:
                net += s["amount"]
    net += net_balance_from_standalone(user_id)
    return round(net, 2)


def get_all_friends():
    """Get all unique friends across all trips for the current user."""
    trips = get_user_trips()
    friend_ids = set()
    for trip in trips:
        members = TripMember.query.filter_by(trip_id=trip.id).all()
        for m in members:
            if m.user_id != current_user.id:
                friend_ids.add(m.user_id)
    friend_ids.update(friend_user_ids_from_standalone(current_user.id))
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

    apply_standalone_to_pairwise_balances(
        current_user.id,
        current_user.name,
        net_balance,
    )

    return dict(net_balance)


def enrich_settle_suggestion(raw: dict) -> dict:
    """Attach button URL/label for a raw pairwise suggestion."""
    trip_ids = raw["trip_ids"]
    viewer_id = current_user.id
    other_id = raw["other_user_id"]
    trip_id = trip_ids[0] if trip_ids else None

    if raw["viewer_owes"]:
        link = find_pending_payment_link(viewer_id, other_id, trip_ids)
        raw["button_label"] = "Pay your share"
        if link:
            raw["button_url"] = build_guest_payment_url_for_link(link)
            raw["payment_link_id"] = link.id
        elif trip_id:
            raw["button_url"] = url_for("settlement", trip_id=trip_id)
        else:
            raw["button_url"] = url_for("collect")
    else:
        link = find_pending_payment_link(other_id, viewer_id, trip_ids)
        raw["button_label"] = "Open payment link"
        if link:
            raw["button_url"] = url_for("collect", highlight=link.id)
            raw["payment_link_id"] = link.id
            raw["copy_url"] = build_guest_payment_url_for_link(link)
        elif trip_id:
            raw["button_url"] = url_for("settlement", trip_id=trip_id)
        else:
            raw["button_url"] = url_for("collect")

    return raw


def settle_suggestion_for_friend(friend, shared_trip_ids: list[int]) -> dict | None:
    threshold = app.config["SETTLE_SUGGESTION_THRESHOLD"]
    raw = build_pairwise_suggestion_raw(
        current_user.id,
        friend.id,
        friend.name,
        shared_trip_ids,
        threshold,
    )
    if raw is None:
        return None
    return enrich_settle_suggestion(raw)


def settle_suggestions_for_trip(trip_id: int, members: list) -> list[dict]:
    threshold = app.config["SETTLE_SUGGESTION_THRESHOLD"]
    trip_ids = [trip_id]
    out = []
    for member in members:
        if member.id == current_user.id:
            continue
        raw = build_pairwise_suggestion_raw(
            current_user.id,
            member.id,
            member.name,
            trip_ids,
            threshold,
        )
        if raw:
            out.append(enrich_settle_suggestion(raw))
    return out


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

    # Also compute per-trip breakdown
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

    settle_suggestion = settle_suggestion_for_friend(friend, shared_trip_ids)

    return render_template(
        "friend_detail.html",
        friend=friend,
        shared_expenses=shared_expenses,
        total_spent=total_spent,
        per_trip=per_trip,
        settle_suggestion=settle_suggestion,
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

@app.route("/notifications/read-all", methods=["POST"])
@login_required
def notifications_read_all():
    mark_all_read(current_user.id)
    db.session.commit()
    flash("Notifications cleared.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def notification_mark_read(notification_id):
    from notifications import mark_read

    mark_read(notification_id, current_user.id)
    db.session.commit()
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html")


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


@app.route("/groups/<int:trip_id>/activity")
@app.route("/trips/<int:trip_id>/activity")
@login_required
def activity_trip(trip_id):
    trip = get_trip_or_redirect(trip_id)
    if trip is None:
        return redirect(url_for("dashboard"))
    page = request.args.get("page", 1, type=int)
    pagination = paginate_activity(trip_id=trip.id, page=page, per_page=20)
    return render_template(
        "activity.html",
        pagination=pagination,
        scope="trip",
        trip=trip,
    )


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
    payment_hub = get_payment_hub_for_user(current_user.id)
    net_balance = get_user_net_balance(current_user.id)
    chart_pending = round(payment_hub["pending_total"] * float(session.get("conversion_rate", 1.0)), 2)
    chart_collected = round(payment_hub["collected_total"] * float(session.get("conversion_rate", 1.0)), 2)
    recent_receipts = (
        Expense.query.filter(
            Expense.paid_by == current_user.id,
            Expense.receipt_image_url.isnot(None),
        )
        .order_by(Expense.created_at.desc())
        .limit(5)
        .all()
    )
    first_active = active_trips[0] if active_trips else (trips[0] if trips else None)
    fab_trip = first_active if first_active and first_active.is_active else None
    fab_members = get_trip_members(fab_trip.id) if fab_trip else []
    trip_ids = [t.id for t in trips]
    activity_items = recent_activity_for_user(current_user.id, trip_ids, limit=8)

    return render_template(
        "dashboard.html",
        expenses=expenses[:5],
        total_expenses=round(total_expenses, 2),
        friend_count=max(len(friends) - 1, 0),
        user_spend_per_trip=user_spend_per_trip,
        trip_names=trip_names,
        payment_hub=payment_hub,
        net_balance=net_balance,
        chart_pending=chart_pending,
        chart_collected=chart_collected,
        recent_receipts=recent_receipts,
        first_active=first_active,
        fab_trip=fab_trip,
        fab_members=fab_members,
        activity_items=activity_items,
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


@app.route("/groups/<int:trip_id>/toggle-active", methods=["POST"])
@app.route("/trips/<int:trip_id>/toggle-active", methods=["POST"])
@login_required
def toggle_group_active(trip_id):
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
        return redirect(url_for("dashboard_groups"))
    return redirect(url_for("group_details", trip_id=trip.id))


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
    range_key = parse_range_key(request.args.get("range"))
    trips, all_expenses = get_user_expenses()
    expenses = filter_expenses_by_range(all_expenses, range_key)
    total_expenses = round(sum(expense.amount or 0 for expense in expenses), 2)
    trip_names = {trip.id: trip.trip_name for trip in trips}

    category_labels, category_values = aggregate_category_spending(expenses, trip_names)
    trip_labels, trip_values = aggregate_trip_spending(expenses, trip_names)
    trend_labels, trend_values = aggregate_spending_trend(expenses)

    friends = get_all_friends()

    return render_template(
        "analytics.html",
        range_key=range_key,
        total_expenses=total_expenses,
        expense_count=len(expenses),
        category_labels=category_labels,
        category_values=category_values,
        trip_labels=trip_labels,
        trip_values=trip_values,
        trend_labels=trend_labels,
        trend_values=trend_values,
        friend_count=len(friends),
        trip_count=len(trips),
    )


@app.route("/splits/new", methods=["GET", "POST"])
@login_required
def new_split():
    """One-off receipt split: scan items → guest links (never tied to a group)."""
    members_for_form = get_split_candidate_users()

    if request.method == "POST":
        member_ids = parse_participant_ids_from_form(
            request.form, current_user.id
        )

        try:
            expense = create_expense_from_form(
                request.form,
                request.files.get("receipt"),
                payer_user_id=current_user.id,
                member_ids=member_ids,
                trip_id=None,
                app=app,
                save_receipt_fn=save_receipt_file,
                log_created_fn=log_expense_created,
            )
            db.session.commit()
            flash("Split created — copy each person's link below.", "success")
            return redirect(expense_detail_url(expense))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for("new_split"))

    return render_template(
        "new_split.html",
        members=members_for_form,
        expense_form_action=url_for("new_split"),
        scan_receipt_url=url_for("scan_receipt_standalone"),
    )


@app.route("/splits/scan-receipt", methods=["POST"])
@login_required
def scan_receipt_standalone():
    """OCR receipt for the new-split flow (no split group required)."""
    return _scan_receipt_json_response()


def _scan_receipt_json_response():
    if not app.config.get("RECEIPT_OCR_ENABLED", True):
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
        validate_receipt_file(receipt_file, app.config["RECEIPT_MAX_BYTES"])
        image_bytes = receipt_file.read()
    except ValueError as exc:
        return jsonify(
            {"success": False, "confidence": "none", "message": str(exc), "items": []}
        ), 400

    tesseract_cmd = app.config.get("TESSERACT_CMD") or None
    result = scan_receipt_image(image_bytes, tesseract_cmd=tesseract_cmd)
    return jsonify(result.to_dict())


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
        db.session.add(TripMember(trip_id=trip.id, user_id=current_user.id))
        log_member_joined(trip.id, current_user.id, trip.trip_name)
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

    db.session.add(TripMember(trip_id=trip.id, user_id=current_user.id))
    log_member_joined(trip.id, current_user.id, trip.trip_name)
    notify_trip_members_of_join(trip.id, current_user.id, trip.trip_name)
    db.session.commit()
    flash("Joined group successfully.", "success")
    return redirect(url_for("group_details", trip_id=trip.id))


@app.route("/groups/<int:trip_id>")
@app.route("/trips/<int:trip_id>")
@login_required
def group_details(trip_id):
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

    activity_items = recent_activity(trip_id=trip.id, limit=10)

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
        expense_pay_links=expense_pay_links,
        open_new_expense=request.args.get("new") == "1",
        activity_items=activity_items,
        expense_form_action=url_for("add_expense", trip_id=trip.id),
        scan_receipt_url=url_for("scan_receipt", trip_id=trip.id),
    )


@app.route("/groups/<int:trip_id>/expenses/add", methods=["GET", "POST"])
@app.route("/trips/<int:trip_id>/expenses/add", methods=["GET", "POST"])
@login_required
def add_expense(trip_id):
    trip = get_trip_or_redirect(trip_id)
    if trip is None:
        return redirect(url_for("dashboard"))

    members = get_trip_members(trip.id)
    member_ids = [member.id for member in members]

    if request.method == "POST":
        try:
            expense = create_expense_from_form(
                request.form,
                request.files.get("receipt"),
                payer_user_id=current_user.id,
                member_ids=member_ids,
                trip_id=trip.id,
                app=app,
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
            flash(str(exc), "error")
            return redirect(url_for("group_details", trip_id=trip.id))

    return render_template(
        "add_expense.html",
        trip=trip,
        members=members,
        expense_form_action=url_for("add_expense", trip_id=trip.id),
        scan_receipt_url=url_for("scan_receipt", trip_id=trip.id),
    )


@app.route("/groups/<int:trip_id>/expenses/scan-receipt", methods=["POST"])
@app.route("/trips/<int:trip_id>/expenses/scan-receipt", methods=["POST"])
@login_required
def scan_receipt(trip_id):
    """OCR a receipt image and return parsed line items (JSON) for form pre-fill."""
    trip = get_trip_or_redirect(trip_id)
    if trip is None:
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
    trip = get_trip_or_redirect(trip_id)
    if trip is None:
        return redirect(url_for("dashboard"))

    expense = Expense.query.filter_by(id=expense_id, trip_id=trip.id).first_or_404()
    return _render_expense_detail(expense, trip)


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
                    "total": round_money(item.price * item.quantity),
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
                    "total": round_money(item.price * item.quantity),
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
    )


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
                "line_total": round_money(float(item.price or 0) * float(item.quantity or 1)),
                "quantity": float(item.quantity or 1),
                "shared_with": others,
            }
        )

    confirmed = bool(link.items_claimed_at)
    finalized = bool(getattr(expense, "claims_finalized_at", None))
    show_pay = (
        finalized
        and link.status != PAYMENT_STATUS_PAID
        and (link.amount_owed or 0) > 0.01
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
                and link.amount_owed > 0.01
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
            flash(str(exc), "error")
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
        khalti_enabled=khalti_configured(app),
        stripe_enabled=stripe_configured(app),
        payment_confirmed=payment_confirmed or link.status == PAYMENT_STATUS_PAID,
        toast_msg=toast_msg,
    )


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
        return jsonify({"ok": False, "message": str(exc)}), 400
    return jsonify({"ok": True, "total": total})


@app.route("/groups/<int:trip_id>/expenses/<int:expense_id>/finalize-claims", methods=["POST"])
@app.route("/trips/<int:trip_id>/expenses/<int:expense_id>/finalize-claims", methods=["POST"])
@login_required
def finalize_claims_route(trip_id, expense_id):
    trip = get_trip_or_redirect(trip_id)
    if trip is None:
        return redirect(url_for("dashboard"))
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


def _finalize_expense_claims_handler(expense: Expense):
    if expense.paid_by != current_user.id:
        flash("Only the person who paid can finalize claims.", "error")
        return redirect(expense_detail_url(expense))

    member_ids = get_expense_member_ids(expense)
    try:
        finalize_expense_claims(expense, member_ids)
        db.session.commit()
        flash("Everyone's shares are calculated — payment links are ready to send.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(expense_detail_url(expense))


@app.route("/pay/<path:token>", methods=["GET"])
def guest_pay(token):
    """Public payment page for a single guest share (no login)."""
    link = get_payment_link_for_token(token)
    if link is None:
        return render_template("pay_guest.html", invalid=True), 404

    expense = link.expense
    if expense is None:
        return render_template("pay_guest.html", invalid=True), 404

    trip = Trip.query.get(expense.trip_id)
    payer = User.query.get(expense.paid_by)
    guest = link.user

    # Khalti redirects here with ?pidx=... — confirm server-side, not from status= query params.
    pidx = request.args.get("pidx", "").strip()
    if pidx and khalti_configured(app):
        if link.status != PAYMENT_STATUS_PAID:
            settled, detail = settle_link_via_khalti_lookup(link, pidx)
            if settled:
                return redirect(
                    url_for("guest_pay", token=token, payment_confirmed="1")
                )
            flash(f"Payment not confirmed yet ({detail}).", "error")
        else:
            return redirect(
                url_for("guest_pay", token=token, payment_confirmed="1")
            )

    payment_confirmed = request.args.get("payment_confirmed") == "1"

    session_id = request.args.get("session_id", "").strip()
    if session_id and stripe_configured(app) and link.status != PAYMENT_STATUS_PAID:
        settled, _detail = settle_link_via_stripe_session(link, session_id)
        if settled:
            return redirect(
                url_for("guest_pay", token=token, payment_confirmed="1")
            )

    return render_template(
        "pay_guest.html",
        invalid=False,
        link=link,
        expense=expense,
        trip=trip,
        payer=payer,
        guest=guest,
        token=token,
        khalti_enabled=khalti_configured(app),
        stripe_enabled=stripe_configured(app),
        payment_confirmed=payment_confirmed,
    )


@app.route("/pay/<path:token>/mark-paid", methods=["POST"])
def guest_pay_mark_paid(token):
    link = get_payment_link_for_token(token)
    if link is None:
        return render_template("pay_guest.html", invalid=True), 404
    if link.status == PAYMENT_STATUS_PAID:
        return redirect(
            url_for("guest_pay", token=token, payment_confirmed="1")
        )

    mark_payment_link_paid(link, "manual")
    return redirect(url_for("guest_pay", token=token, payment_confirmed="1"))


@app.route("/pay/<path:token>/pay-now", methods=["POST"])
@app.route("/pay/<path:token>/khalti", methods=["POST"])
def guest_pay_now(token):
    """Create a Khalti payment session for the exact amount owed."""
    link = get_payment_link_for_token(token)
    if link is None:
        return render_template("pay_guest.html", invalid=True), 404
    if link.status == PAYMENT_STATUS_PAID:
        return redirect(
            url_for("guest_pay", token=token, payment_confirmed="1")
        )

    if not khalti_configured(app):
        flash(
            "Khalti is not configured. Add test KHALTI_SECRET_KEY to .env "
            "(see config.py comments) or use Mark as paid.",
            "error",
        )
        return redirect(url_for("guest_pay", token=token))

    expense = link.expense
    guest = link.user
    return_url = url_for("guest_pay", token=token, _external=True)
    website_url = url_for("home", _external=True)
    order_id = khalti_purchase_order_id(link.id)

    try:
        data = initiate_khalti_payment(
            secret_key=app.config["KHALTI_SECRET_KEY"],
            amount_rupees=link.amount_owed,
            purchase_order_id=order_id,
            purchase_order_name=expense.description or "Split Bills expense",
            return_url=return_url,
            website_url=website_url,
            customer_name=guest.name if guest else "Guest",
        )
        link.khalti_pidx = data["pidx"]
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f"Could not start payment: {exc}", "error")
        return redirect(url_for("guest_pay", token=token))

    return redirect(data["payment_url"])


@app.route("/pay/<path:token>/stripe", methods=["POST"])
def guest_pay_stripe(token):
    link = get_payment_link_for_token(token)
    if link is None:
        return render_template("pay_guest.html", invalid=True), 404
    if link.status == PAYMENT_STATUS_PAID:
        return redirect(url_for("guest_pay", token=token, payment_confirmed="1"))

    if not stripe_configured(app):
        flash("Card checkout is not configured. Use Khalti or mark as paid.", "error")
        return redirect(url_for("guest_pay", token=token))

    expense = link.expense
    guest = link.user
    success_url = url_for("guest_pay", token=token, _external=True)
    cancel_url = url_for("guest_pay", token=token, _external=True)

    try:
        data = create_checkout_session(
            secret_key=app.config["STRIPE_SECRET_KEY"],
            amount_rupees=link.amount_owed,
            currency=app.config.get("STRIPE_CURRENCY") or app.config.get("DEFAULT_CURRENCY", "npr"),
            payment_link_id=link.id,
            product_name=expense.description or "Shared expense",
            customer_email=guest.email if guest and guest.email else None,
            success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=cancel_url,
        )
        link.stripe_checkout_session_id = data["id"]
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f"Could not start card checkout: {exc}", "error")
        return redirect(url_for("guest_pay", token=token))

    return redirect(data["url"])


@app.route("/webhooks/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    wh_secret = app.config.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not wh_secret:
        return {"error": "webhook not configured"}, 503

    stripe = __import__("stripe")
    stripe.api_key = app.config["STRIPE_SECRET_KEY"]
    try:
        event = stripe.Webhook.construct_event(payload, sig, wh_secret)
    except Exception as exc:
        return {"error": str(exc)}, 400

    if event["type"] == "checkout.session.completed":
        sess = event["data"]["object"]
        link_id = (sess.get("metadata") or {}).get("payment_link_id") or sess.get(
            "client_reference_id"
        )
        try:
            link_id_int = int(link_id)
        except (TypeError, ValueError):
            return {"error": "bad link id"}, 400
        link = ExpensePaymentLink.query.get(link_id_int)
        if link and link.status != PAYMENT_STATUS_PAID:
            settle_link_via_stripe_session(link, sess["id"])

    return {"received": True}, 200


@app.route("/webhooks/khalti", methods=["POST"])
def khalti_webhook():
    """Server callback: confirm payment via Khalti lookup (do not trust body alone)."""
    configured_secret = app.config.get("KHALTI_WEBHOOK_SECRET", "").strip()
    if configured_secret:
        incoming = (
            request.headers.get("X-Khalti-Webhook-Secret")
            or request.headers.get("X-Webhook-Secret")
            or ""
        ).strip()
        if incoming != configured_secret:
            return {"error": "unauthorized webhook"}, 401

    payload = request.get_json(silent=True) or {}
    pidx = (payload.get("pidx") or request.form.get("pidx") or "").strip()
    purchase_order_id = (
        payload.get("purchase_order_id")
        or request.form.get("purchase_order_id")
        or ""
    ).strip()

    if not pidx:
        return {"error": "missing pidx"}, 400

    link = ExpensePaymentLink.query.filter_by(khalti_pidx=pidx).first()
    if link is None and purchase_order_id:
        link_id = parse_purchase_order_link_id(purchase_order_id)
        if link_id:
            link = ExpensePaymentLink.query.get(link_id)

    if link is None:
        return {"error": "payment link not found"}, 404

    if not khalti_configured(app):
        return {"error": "khalti not configured"}, 503

    settled, detail = settle_link_via_khalti_lookup(link, pidx)
    if settled:
        return {"status": "paid", "detail": detail}, 200
    return {"status": "pending", "detail": detail}, 202


@app.route("/groups/<int:trip_id>/settlement")
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
    settle_suggestions = settle_suggestions_for_trip(trip.id, members)

    return render_template(
        "settlement.html",
        trip=trip,
        settlements=settlements,
        settle_suggestions=settle_suggestions,
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
            "is_recurring": (
                "ALTER TABLE expenses ADD COLUMN is_recurring TINYINT(1) NOT NULL DEFAULT 0"
            ),
            "recurrence_interval": (
                "ALTER TABLE expenses ADD COLUMN recurrence_interval VARCHAR(20) NULL"
            ),
            "next_occurrence_date": (
                "ALTER TABLE expenses ADD COLUMN next_occurrence_date DATE NULL"
            ),
            "self_service_items": (
                "ALTER TABLE expenses ADD COLUMN self_service_items TINYINT(1) NOT NULL DEFAULT 0"
            ),
            "claims_finalized_at": (
                "ALTER TABLE expenses ADD COLUMN claims_finalized_at DATETIME NULL"
            ),
        },
        "expense_payment_links": {
            "stripe_checkout_session_id": (
                "ALTER TABLE expense_payment_links "
                "ADD COLUMN stripe_checkout_session_id VARCHAR(255) NULL"
            ),
            "items_claimed_at": (
                "ALTER TABLE expense_payment_links ADD COLUMN items_claimed_at DATETIME NULL"
            ),
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


@app.cli.command("send-payment-reminders")
def send_payment_reminders_command():
    """Manually run the payment reminder job (same logic as the daily scheduler)."""
    from reminders import run_payment_reminder_job

    stats = run_payment_reminder_job(app, mail)
    print(f"Reminder job stats: {stats}")


init_scheduler_for_app(app, mail)


if __name__ == "__main__":
    init_database()
    app.run(
        debug=True,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", 5002)),
    )
   