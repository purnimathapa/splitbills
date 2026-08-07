"""Tests for group role permissions (logic + enforcement)."""

import os
import unittest

os.environ["DATABASE_URL"] = "sqlite://"

from sqlalchemy.pool import StaticPool

from app import app, bcrypt, db
from flask_login import login_user
from group_permissions import (
    PERM_ADD_EXPENSE,
    PERM_ARCHIVE_GROUP,
    PERM_MANAGE_MEMBERS,
    PERM_MANAGE_SETTINGS,
    PERM_TRANSFER_OWNERSHIP,
    PERM_VIEW_GROUP,
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_OWNER,
    can,
    can_assign_role,
    can_remove_member,
)
from models import Trip, TripMember, User
from services.trip_access import get_membership, require_trip_permission
from werkzeug.exceptions import Forbidden, NotFound


class GroupPermissionLogicTests(unittest.TestCase):
    def test_member_can_view_not_manage(self):
        self.assertTrue(can(ROLE_MEMBER, PERM_VIEW_GROUP))
        self.assertTrue(can(ROLE_MEMBER, PERM_ADD_EXPENSE))
        self.assertFalse(can(ROLE_MEMBER, PERM_MANAGE_SETTINGS))
        self.assertFalse(can(ROLE_MEMBER, PERM_ARCHIVE_GROUP))

    def test_admin_can_manage_not_archive(self):
        self.assertTrue(can(ROLE_ADMIN, PERM_MANAGE_SETTINGS))
        self.assertTrue(can(ROLE_ADMIN, PERM_MANAGE_MEMBERS))
        self.assertFalse(can(ROLE_ADMIN, PERM_ARCHIVE_GROUP))
        self.assertFalse(can(ROLE_ADMIN, PERM_TRANSFER_OWNERSHIP))

    def test_owner_has_full_group_control(self):
        self.assertTrue(can(ROLE_OWNER, PERM_MANAGE_SETTINGS))
        self.assertTrue(can(ROLE_OWNER, PERM_ARCHIVE_GROUP))
        self.assertTrue(can(ROLE_OWNER, PERM_TRANSFER_OWNERSHIP))

    def test_cannot_assign_owner_via_role_change(self):
        self.assertFalse(can_assign_role(ROLE_OWNER, ROLE_OWNER))
        self.assertTrue(can_assign_role(ROLE_ADMIN, ROLE_ADMIN))

    def test_cannot_remove_owner(self):
        self.assertFalse(can_remove_member(ROLE_OWNER, ROLE_OWNER))
        self.assertFalse(can_remove_member(ROLE_ADMIN, ROLE_OWNER))
        self.assertTrue(can_remove_member(ROLE_ADMIN, ROLE_MEMBER))


class GroupPermissionEnforcementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_ENGINE_OPTIONS={
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            },
            REMINDER_JOB_ENABLED=False,
            RECURRING_JOB_ENABLED=False,
        )
        cls.ctx = cls.app.app_context()
        cls.ctx.push()
        db.engine.dispose()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.ctx.pop()

    def setUp(self):
        db.session.remove()
        db.session.query(TripMember).delete(synchronize_session=False)
        db.session.query(Trip).delete(synchronize_session=False)
        db.session.query(User).delete(synchronize_session=False)
        db.session.commit()

        password = bcrypt.generate_password_hash("secret").decode("utf-8")
        self.owner = User(name="Owner", email="owner@test.com", password=password)
        self.admin = User(name="Admin", email="admin@test.com", password=password)
        self.member = User(name="Member", email="member@test.com", password=password)
        self.outsider = User(name="Outsider", email="out@test.com", password=password)
        db.session.add_all([self.owner, self.admin, self.member, self.outsider])
        db.session.flush()

        self.trip = Trip(
            trip_name="Test Group",
            invite_code="JOIN01",
            created_by=self.owner.id,
            is_active=True,
        )
        db.session.add(self.trip)
        db.session.flush()
        db.session.add_all(
            [
                TripMember(trip_id=self.trip.id, user_id=self.owner.id, role=ROLE_OWNER),
                TripMember(trip_id=self.trip.id, user_id=self.admin.id, role=ROLE_ADMIN),
                TripMember(trip_id=self.trip.id, user_id=self.member.id, role=ROLE_MEMBER),
            ]
        )
        db.session.commit()

    def _as_user(self, user: User):
        login_user(user)

    def test_member_can_view_group(self):
        with self.app.test_request_context():
            self._as_user(self.member)
            trip, membership = require_trip_permission(self.trip.id, PERM_VIEW_GROUP)
            self.assertEqual(trip.id, self.trip.id)
            self.assertEqual(membership.role, ROLE_MEMBER)

    def test_outsider_cannot_view_group(self):
        with self.app.test_request_context():
            self._as_user(self.outsider)
            with self.assertRaises(Forbidden):
                require_trip_permission(self.trip.id, PERM_VIEW_GROUP)

    def test_member_cannot_archive_group(self):
        with self.app.test_request_context():
            self._as_user(self.member)
            with self.assertRaises(Forbidden):
                require_trip_permission(self.trip.id, PERM_ARCHIVE_GROUP)

    def test_owner_can_archive_group(self):
        with self.app.test_request_context():
            self._as_user(self.owner)
            trip, _membership = require_trip_permission(self.trip.id, PERM_ARCHIVE_GROUP)
            self.assertEqual(trip.id, self.trip.id)

    def test_admin_can_manage_settings(self):
        with self.app.test_request_context():
            self._as_user(self.admin)
            trip, membership = require_trip_permission(self.trip.id, PERM_MANAGE_SETTINGS)
            self.assertEqual(membership.role, ROLE_ADMIN)

    def test_member_cannot_manage_settings(self):
        with self.app.test_request_context():
            self._as_user(self.member)
            with self.assertRaises(Forbidden):
                require_trip_permission(self.trip.id, PERM_MANAGE_SETTINGS)

    def test_admin_can_manage_members(self):
        with self.app.test_request_context():
            self._as_user(self.admin)
            require_trip_permission(self.trip.id, PERM_MANAGE_MEMBERS)

    def test_member_cannot_manage_members(self):
        with self.app.test_request_context():
            self._as_user(self.member)
            with self.assertRaises(Forbidden):
                require_trip_permission(self.trip.id, PERM_MANAGE_MEMBERS)

    def test_owner_can_transfer_ownership(self):
        with self.app.test_request_context():
            self._as_user(self.owner)
            require_trip_permission(self.trip.id, PERM_TRANSFER_OWNERSHIP)

    def test_admin_cannot_transfer_ownership(self):
        with self.app.test_request_context():
            self._as_user(self.admin)
            with self.assertRaises(Forbidden):
                require_trip_permission(self.trip.id, PERM_TRANSFER_OWNERSHIP)

    def test_member_can_add_expense_when_active(self):
        with self.app.test_request_context():
            self._as_user(self.member)
            require_trip_permission(self.trip.id, PERM_ADD_EXPENSE)

    def test_invalid_group_id_returns_404(self):
        with self.app.test_request_context():
            self._as_user(self.owner)
            with self.assertRaises(NotFound):
                require_trip_permission(99999, PERM_VIEW_GROUP)

    def test_get_membership_returns_role(self):
        with self.app.test_request_context():
            self._as_user(self.admin)
            row = get_membership(self.trip.id)
            self.assertIsNotNone(row)
            self.assertEqual(row.role, ROLE_ADMIN)


class GroupPermissionHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_ENGINE_OPTIONS={
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            },
            REMINDER_JOB_ENABLED=False,
            RECURRING_JOB_ENABLED=False,
        )
        cls.ctx = cls.app.app_context()
        cls.ctx.push()
        db.engine.dispose()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.ctx.pop()

    def setUp(self):
        db.session.remove()
        self.client = self.app.test_client()
        db.session.query(TripMember).delete(synchronize_session=False)
        db.session.query(Trip).delete(synchronize_session=False)
        db.session.query(User).delete(synchronize_session=False)
        db.session.commit()

        password = bcrypt.generate_password_hash("secret").decode("utf-8")
        owner = User(name="Owner", email="owner@test.com", password=password)
        admin = User(name="Admin", email="admin@test.com", password=password)
        member = User(name="Member", email="member@test.com", password=password)
        db.session.add_all([owner, admin, member])
        db.session.flush()
        trip = Trip(
            trip_name="Test Group",
            invite_code="JOIN01",
            created_by=owner.id,
            is_active=True,
        )
        db.session.add(trip)
        db.session.flush()
        db.session.add_all(
            [
                TripMember(trip_id=trip.id, user_id=owner.id, role=ROLE_OWNER),
                TripMember(trip_id=trip.id, user_id=admin.id, role=ROLE_ADMIN),
                TripMember(trip_id=trip.id, user_id=member.id, role=ROLE_MEMBER),
            ]
        )
        trip_id = trip.id
        admin_id = admin.id
        member_id = member.id
        db.session.commit()
        self.trip_id = trip_id
        self.admin_id = admin_id
        self.member_id = member_id
        self._password = "secret"

    def tearDown(self):
        db.session.rollback()
        db.session.remove()

    def _login(self, email: str):
        return self.client.post(
            "/login",
            data={"email": email, "password": self._password},
            follow_redirects=False,
        )

    def test_admin_can_remove_member_via_http(self):
        self._login("admin@test.com")
        response = self.client.post(
            f"/groups/{self.trip_id}/members/{self.member_id}/remove",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(
            TripMember.query.filter_by(
                trip_id=self.trip_id,
                user_id=self.member_id,
            ).first()
        )


if __name__ == "__main__":
    unittest.main()
