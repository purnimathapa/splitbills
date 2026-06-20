from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()


class User(UserMixin, db.Model):

    __tablename__ = "users"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(100),
        nullable=False
    )

    email = db.Column(
        db.String(120),
        unique=True,
        nullable=False
    )

    password = db.Column(
        db.String(255),
        nullable=False
    )

    profile_pic = db.Column(
        db.String(255),
        default="default.png"
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


class Trip(db.Model):

    __tablename__ = "trips"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    trip_name = db.Column(
        db.String(150),
        nullable=False
    )

    invite_code = db.Column(
        db.String(10),
        unique=True
    )

    created_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id")
    )

    is_active = db.Column(
        db.Boolean,
        default=True
    )

    description = db.Column(
        db.Text
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


class TripMember(db.Model):

    __tablename__ = "trip_members"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    trip_id = db.Column(
        db.Integer,
        db.ForeignKey("trips.id")
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id")
    )


class Expense(db.Model):

    __tablename__ = "expenses"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    trip_id = db.Column(
        db.Integer,
        db.ForeignKey("trips.id")
    )

    paid_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id")
    )

    category = db.Column(
        db.String(100)
    )

    description = db.Column(
        db.String(255)
    )

    amount = db.Column(
        db.Float
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    payer = db.relationship(
        "User"
    )