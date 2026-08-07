from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

from group_permissions import ROLE_MEMBER
from money import MONEY_COLUMN, PERCENT_COLUMN, QUANTITY_COLUMN

db = SQLAlchemy()

# Expense split strategies (stored on expenses.split_type)
SPLIT_TYPE_EQUAL = "equal"
SPLIT_TYPE_EXACT = "exact"
SPLIT_TYPE_PERCENTAGE = "percentage"
SPLIT_TYPE_SHARES = "shares"
SPLIT_TYPE_ITEMIZED = "itemized"

SPLIT_TYPES = (
    SPLIT_TYPE_EQUAL,
    SPLIT_TYPE_EXACT,
    SPLIT_TYPE_PERCENTAGE,
    SPLIT_TYPE_SHARES,
    SPLIT_TYPE_ITEMIZED,
)

RECURRENCE_WEEKLY = "weekly"
RECURRENCE_MONTHLY = "monthly"

RECURRENCE_INTERVALS = (RECURRENCE_WEEKLY, RECURRENCE_MONTHLY)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    profile_pic = db.Column(db.String(255), default="default.png")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Trip(db.Model):
    """Ongoing split group (rent, trip, event). DB table remains ``trips``."""

    __tablename__ = "trips"

    id = db.Column(db.Integer, primary_key=True)
    trip_name = db.Column(db.String(150), nullable=False)
    invite_code = db.Column(db.String(10), unique=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    is_active = db.Column(db.Boolean, default=True)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TripMember(db.Model):
    __tablename__ = "trip_members"

    id = db.Column(db.Integer, primary_key=True)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    role = db.Column(db.String(20), nullable=False, default=ROLE_MEMBER)


class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id"), nullable=True)
    paid_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    category = db.Column(db.String(100))
    description = db.Column(db.String(255))
    amount = db.Column(MONEY_COLUMN)
    remarks = db.Column(db.String(255))
    split_type = db.Column(
        db.String(20),
        nullable=False,
        default=SPLIT_TYPE_EQUAL,
    )
    tax_tip_amount = db.Column(MONEY_COLUMN, default=0)
    receipt_image_url = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_recurring = db.Column(db.Boolean, nullable=False, default=False)
    recurrence_interval = db.Column(db.String(20), nullable=True)
    next_occurrence_date = db.Column(db.Date, nullable=True)
    recurrence_end_date = db.Column(db.Date, nullable=True)
    recurring_template_id = db.Column(
        db.Integer,
        db.ForeignKey("expenses.id"),
        nullable=True,
    )
    recurrence_occurrence_date = db.Column(db.Date, nullable=True)
    self_service_items = db.Column(db.Boolean, nullable=False, default=False)
    claims_finalized_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint(
            "recurring_template_id",
            "recurrence_occurrence_date",
            name="uq_expense_recurring_occurrence",
        ),
        db.Index("idx_expenses_trip_created", "trip_id", "created_at"),
        db.Index("idx_expenses_paid_by_created", "paid_by", "created_at"),
        db.Index("idx_expenses_created_at", "created_at"),
        db.Index("idx_expenses_is_recurring", "is_recurring"),
    )

    payer = db.relationship("User", foreign_keys=[paid_by])
    splits = db.relationship(
        "ExpenseSplit",
        back_populates="expense",
        cascade="all, delete-orphan",
    )
    items = db.relationship(
        "ExpenseItem",
        back_populates="expense",
        cascade="all, delete-orphan",
    )
    participants = db.relationship(
        "ExpenseParticipant",
        back_populates="expense",
        cascade="all, delete-orphan",
    )


class ExpenseParticipant(db.Model):
    """People on a one-off expense (when ``trip_id`` is null)."""

    __tablename__ = "expense_participants"
    __table_args__ = (
        db.UniqueConstraint(
            "expense_id",
            "user_id",
            name="uq_expense_participants_expense_user",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(
        db.Integer,
        db.ForeignKey("expenses.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    expense = db.relationship("Expense", back_populates="participants")
    user = db.relationship("User")


class ExpenseSplit(db.Model):
    """Per-participant share of a single expense.

    amount_owed is always the resolved currency amount for settlement.
    percentage and shares store user input for percentage- and share-based splits.
    """

    __tablename__ = "expense_splits"
    __table_args__ = (
        db.UniqueConstraint(
            "expense_id",
            "user_id",
            name="uq_expense_splits_expense_user",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(
        db.Integer,
        db.ForeignKey("expenses.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    amount_owed = db.Column(MONEY_COLUMN, nullable=False)
    percentage = db.Column(PERCENT_COLUMN, nullable=True)
    shares = db.Column(PERCENT_COLUMN, nullable=True)

    expense = db.relationship("Expense", back_populates="splits")
    user = db.relationship("User")


class ExpenseItem(db.Model):
    __tablename__ = "expense_items"

    id = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(
        db.Integer,
        db.ForeignKey("expenses.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = db.Column(db.String(255), nullable=False)
    price = db.Column(MONEY_COLUMN, nullable=False)
    quantity = db.Column(QUANTITY_COLUMN, nullable=False, default=1)

    expense = db.relationship("Expense", back_populates="items")
    assignments = db.relationship(
        "ExpenseItemAssignment",
        back_populates="item",
        cascade="all, delete-orphan",
    )


class ExpenseItemAssignment(db.Model):
    __tablename__ = "expense_item_assignments"
    __table_args__ = (
        db.UniqueConstraint(
            "expense_item_id",
            "user_id",
            name="uq_expense_item_assignments_item_user",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    expense_item_id = db.Column(
        db.Integer,
        db.ForeignKey("expense_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    item = db.relationship("ExpenseItem", back_populates="assignments")
    user = db.relationship("User")


PAYMENT_STATUS_PENDING = "pending"
PAYMENT_STATUS_PAID = "paid"


class ExpensePaymentLink(db.Model):
    """Shareable pay link for one user's share of one expense."""

    __tablename__ = "expense_payment_links"
    __table_args__ = (
        db.UniqueConstraint(
            "expense_id",
            "user_id",
            name="uq_expense_payment_links_expense_user",
        ),
        db.UniqueConstraint("link_uuid", name="uq_expense_payment_links_uuid"),
        db.Index("idx_payment_links_user_status", "user_id", "status"),
        db.Index("idx_payment_links_expense_status", "expense_id", "status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    link_uuid = db.Column(db.String(36), nullable=False)
    expense_id = db.Column(
        db.Integer,
        db.ForeignKey("expenses.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    amount_owed = db.Column(MONEY_COLUMN, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=PAYMENT_STATUS_PENDING)
    paid_at = db.Column(db.DateTime, nullable=True)
    payment_provider = db.Column(db.String(30), nullable=True)
    khalti_pidx = db.Column(db.String(80), nullable=True)
    stripe_checkout_session_id = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items_claimed_at = db.Column(db.DateTime, nullable=True)

    expense = db.relationship("Expense", backref="payment_links")
    user = db.relationship("User")
    reminder_logs = db.relationship(
        "PaymentReminderLog",
        back_populates="payment_link",
        cascade="all, delete-orphan",
    )


ACTION_EXPENSE_CREATED = "expense_created"
ACTION_PAYMENT_CONFIRMED = "payment_confirmed"
ACTION_MEMBER_JOINED = "member_joined"

ACTIVITY_ACTION_TYPES = (
    ACTION_EXPENSE_CREATED,
    ACTION_PAYMENT_CONFIRMED,
    ACTION_MEMBER_JOINED,
)


class ActivityLog(db.Model):
    """Chronological feed of group and payment events."""

    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id"), nullable=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action_type = db.Column(db.String(40), nullable=False)
    description = db.Column(db.String(512), nullable=False)
    related_expense_id = db.Column(
        db.Integer, db.ForeignKey("expenses.id"), nullable=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    actor = db.relationship("User", foreign_keys=[actor_user_id])
    trip = db.relationship("Trip")
    related_expense = db.relationship("Expense")


class PaymentReminderLog(db.Model):
    """Record of a payment reminder email sent for a guest link."""

    __tablename__ = "payment_reminder_logs"

    id = db.Column(db.Integer, primary_key=True)
    payment_link_id = db.Column(
        db.Integer,
        db.ForeignKey("expense_payment_links.id", ondelete="CASCADE"),
        nullable=False,
    )
    email_to = db.Column(db.String(120), nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    payment_link = db.relationship("ExpensePaymentLink", back_populates="reminder_logs")


NOTIFICATION_PAYMENT_RECEIVED = "payment_received"
NOTIFICATION_TRIP_JOIN = "trip_join"
NOTIFICATION_REMINDER_SENT = "reminder_sent"
NOTIFICATION_GROUP_ADDED = "group_added"
NOTIFICATION_EXPENSE_ADDED = "expense_added"
NOTIFICATION_EXPENSE_UPDATED = "expense_updated"
NOTIFICATION_SETTLEMENT_REQUESTED = "settlement_requested"
NOTIFICATION_SETTLEMENT_COMPLETED = "settlement_completed"
NOTIFICATION_RECURRING_GENERATED = "recurring_generated"


class Notification(db.Model):
    """In-app alerts for the notification bell."""

    __tablename__ = "notifications"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "dedupe_key",
            name="uq_notifications_user_dedupe",
        ),
        db.Index("idx_notifications_user_created", "user_id", "created_at"),
        db.Index("idx_notifications_user_unread", "user_id", "read_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    kind = db.Column(db.String(40), nullable=False, default=NOTIFICATION_PAYMENT_RECEIVED)
    message = db.Column(db.String(512), nullable=False)
    href = db.Column(db.String(512), nullable=True)
    dedupe_key = db.Column(db.String(128), nullable=True)
    read_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("notifications", lazy="dynamic"))
