import os
from datetime import datetime

from sqlalchemy import inspect, text

from flask import Flask, session, url_for
from flask_bcrypt import Bcrypt
from flask_mail import Mail
from flask_login import LoginManager, current_user

from activity_log import ACTIVITY_ICONS
from config import Config
from models import User, db
from notifications import recent_notifications, unread_count
from routes import register_routes
from scheduler_setup import init_scheduler_for_app
from expense_create import EXPENSE_CATEGORIES
from services.balances import get_all_friends, get_user_net_balance
from services.trip_access import get_user_trips


app = Flask(__name__, static_folder="style", static_url_path="/static")
app.config.from_object(Config)

db.init_app(app)
bcrypt = Bcrypt(app)
mail = Mail(app)

app.jinja_env.filters["zip"] = zip

AVATAR_PALETTE = [
    "#2563eb", "#0891b2", "#059669", "#7c3aed",
    "#db2777", "#ea580c", "#4f46e5", "#0d9488",
]


@app.template_filter("avatar_color")
def avatar_color_filter(name):
    """Deterministic avatar background from display name."""
    text = (name or "?").strip() or "?"
    h = 0
    for char in text:
        h = (h * 31 + ord(char)) & 0xFFFFFFFF
    return AVATAR_PALETTE[h % len(AVATAR_PALETTE)]


@app.template_filter("time_ago")
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


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


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


@app.context_processor
def inject_nav_shell():
    if not current_user.is_authenticated:
        return {}
    trips = get_user_trips()
    friends = get_all_friends()
    return {
        "nav_trips": trips,
        "nav_friends": friends,
        "nav_net_balance": get_user_net_balance(current_user.id),
    }


@app.context_processor
def inject_currency():
    default_cur = app.config.get("DEFAULT_CURRENCY", "Rs")
    return {
        "currency": session.get("currency", default_cur),
        "conversion_rate": float(session.get("conversion_rate", 1.0)),
        "expense_categories": EXPENSE_CATEGORIES,
    }


register_routes(app, bcrypt)


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
            "role": (
                "ALTER TABLE trip_members ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'member'"
            ),
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
            "recurrence_end_date": (
                "ALTER TABLE expenses ADD COLUMN recurrence_end_date DATE NULL"
            ),
            "recurring_template_id": (
                "ALTER TABLE expenses ADD COLUMN recurring_template_id INT NULL"
            ),
            "recurrence_occurrence_date": (
                "ALTER TABLE expenses ADD COLUMN recurrence_occurrence_date DATE NULL"
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
