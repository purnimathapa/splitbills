"""
End-to-end QA harness — HTTP-level flows not covered elsewhere.
Run: PYTHONPATH=. python3 -m unittest tests.test_e2e_qa -v
"""

import io
import os
import unittest
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy.pool import StaticPool

from app import app, bcrypt, db
from flask_login import login_user
from group_permissions import ROLE_ADMIN, ROLE_MEMBER, ROLE_OWNER
from models import (
    Expense,
    ExpensePaymentLink,
    ExpenseSplit,
    Notification,
    PAYMENT_STATUS_PENDING,
    Trip,
    TripMember,
    User,
)
from services.analytics import build_user_analytics


class E2EQAHarness(unittest.TestCase):
    """QA engineer E2E checks via Flask test client."""

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
            SECRET_KEY="qa-secret",
            REMINDER_JOB_ENABLED=False,
            RECURRING_JOB_ENABLED=False,
            RECEIPT_OCR_ENABLED=False,
            WTF_CSRF_ENABLED=False,
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
        self.client = self.app.test_client()
        for model in (Notification, ExpensePaymentLink, ExpenseSplit, Expense, TripMember, Trip, User):
            db.session.query(model).delete(synchronize_session=False)
        db.session.commit()
        self._password = "secret123"

    def _register(self, name, email, password=None):
        pw = password or self._password
        return self.client.post(
            "/register",
            data={"name": name, "email": email, "password": pw},
            follow_redirects=False,
        )

    def _login(self, email, password=None):
        pw = password or self._password
        return self.client.post(
            "/login",
            data={"email": email, "password": pw},
            follow_redirects=False,
        )

    def _logout(self):
        return self.client.get("/logout", follow_redirects=False)

    def _make_group(self, owner_email, name="QA Group"):
        owner = User.query.filter_by(email=owner_email).first()
        trip = Trip(trip_name=name, invite_code="QAJOIN1", created_by=owner.id, is_active=True)
        db.session.add(trip)
        db.session.flush()
        db.session.add(TripMember(trip_id=trip.id, user_id=owner.id, role=ROLE_OWNER))
        db.session.commit()
        return trip

    # ── AUTHENTICATION ──

    def test_qa_register_login_logout(self):
        r = self._register("Alice", "alice@qa.com")
        self.assertIn(r.status_code, (302, 303))
        self.assertIsNotNone(User.query.filter_by(email="alice@qa.com").first())

        self._logout()
        r = self._login("alice@qa.com")
        self.assertIn(r.status_code, (302, 303))

        r = self._logout()
        self.assertIn(r.status_code, (302, 303))

    def test_qa_invalid_login(self):
        self._register("Bob", "bob@qa.com")
        self._logout()
        r = self._login("bob@qa.com", password="wrong")
        self.assertIn(r.status_code, (302, 303))
        self.assertIn(b"/login", r.location.encode() if r.location else b"")

    def test_qa_protected_route_redirects_anonymous(self):
        r = self.client.get("/dashboard", follow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        loc = r.location or ""
        self.assertTrue("/login" in loc or "login" in loc.lower())

    def test_qa_duplicate_registration(self):
        self._register("Carol", "carol@qa.com")
        self._logout()
        r = self._register("Carol2", "carol@qa.com")
        self.assertIn(r.status_code, (302, 303))
        self.assertEqual(User.query.filter_by(email="carol@qa.com").count(), 1)

    # ── GROUPS ──

    def test_qa_create_group_via_http(self):
        self._register("Owner", "owner@qa.com")
        r = self.client.post(
            "/groups/create",
            data={"trip_name": "Weekend Trip"},
            follow_redirects=False,
        )
        self.assertIn(r.status_code, (302, 303))
        trip = Trip.query.filter_by(trip_name="Weekend Trip").first()
        self.assertIsNotNone(trip)

    def test_qa_join_group_via_invite(self):
        self._register("Owner", "owner@qa.com")
        trip = self._make_group("owner@qa.com", "Joinable")
        self._logout()

        self._register("Joiner", "joiner@qa.com")
        r = self.client.post(
            "/groups/join",
            data={"invite_code": trip.invite_code},
            follow_redirects=False,
        )
        self.assertIn(r.status_code, (302, 303))
        joiner = User.query.filter_by(email="joiner@qa.com").first()
        self.assertIsNotNone(
            TripMember.query.filter_by(trip_id=trip.id, user_id=joiner.id).first()
        )

    def test_qa_member_cannot_remove_admin(self):
        pw = bcrypt.generate_password_hash(self._password).decode("utf-8")
        owner = User(name="O", email="o@qa.com", password=pw)
        admin = User(name="A", email="a@qa.com", password=pw)
        member = User(name="M", email="m@qa.com", password=pw)
        db.session.add_all([owner, admin, member])
        db.session.flush()
        trip = Trip(trip_name="Roles", invite_code="ROLE01", created_by=owner.id)
        db.session.add(trip)
        db.session.flush()
        db.session.add_all([
            TripMember(trip_id=trip.id, user_id=owner.id, role=ROLE_OWNER),
            TripMember(trip_id=trip.id, user_id=admin.id, role=ROLE_ADMIN),
            TripMember(trip_id=trip.id, user_id=member.id, role=ROLE_MEMBER),
        ])
        db.session.commit()

        self._login("m@qa.com")
        r = self.client.post(
            f"/groups/{trip.id}/members/{admin.id}/remove",
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 403)
        self.assertIsNotNone(
            TripMember.query.filter_by(trip_id=trip.id, user_id=admin.id).first()
        )

    def test_qa_non_member_cannot_view_group(self):
        pw = bcrypt.generate_password_hash(self._password).decode("utf-8")
        owner = User(name="O", email="o2@qa.com", password=pw)
        outsider = User(name="X", email="x@qa.com", password=pw)
        db.session.add_all([owner, outsider])
        db.session.flush()
        trip = Trip(trip_name="Private", invite_code="PRIV01", created_by=owner.id)
        db.session.add(trip)
        db.session.flush()
        db.session.add(TripMember(trip_id=trip.id, user_id=owner.id, role=ROLE_OWNER))
        db.session.commit()

        self._login("x@qa.com")
        r = self.client.get(f"/groups/{trip.id}", follow_redirects=False)
        self.assertIn(r.status_code, (403, 404))

    # ── EXPENSES ──

    def test_qa_create_equal_split_expense(self):
        pw = bcrypt.generate_password_hash(self._password).decode("utf-8")
        a = User(name="A", email="a2@qa.com", password=pw)
        b = User(name="B", email="b2@qa.com", password=pw)
        db.session.add_all([a, b])
        db.session.flush()
        trip = Trip(trip_name="Dinner", invite_code="DIN001", created_by=a.id)
        db.session.add(trip)
        db.session.flush()
        db.session.add_all([
            TripMember(trip_id=trip.id, user_id=a.id, role=ROLE_OWNER),
            TripMember(trip_id=trip.id, user_id=b.id, role=ROLE_MEMBER),
        ])
        db.session.commit()

        self._login("a2@qa.com")
        r = self.client.post(
            f"/groups/{trip.id}/expenses/add",
            data={
                "description": "Pizza night",
                "amount": "100.00",
                "split_method": "equal",
                "paid_by_user_id": str(a.id),
                "participant_user_ids": [str(a.id), str(b.id)],
                "category": "Food",
            },
            follow_redirects=False,
        )
        self.assertIn(r.status_code, (302, 303))
        expense = Expense.query.filter_by(description="Pizza night").first()
        self.assertIsNotNone(expense)
        self.assertEqual(float(expense.amount), 100.0)
        splits = ExpenseSplit.query.filter_by(expense_id=expense.id).all()
        self.assertEqual(len(splits), 2)

    def test_qa_invalid_exact_split_rejected(self):
        pw = bcrypt.generate_password_hash(self._password).decode("utf-8")
        a = User(name="A", email="a3@qa.com", password=pw)
        b = User(name="B", email="b3@qa.com", password=pw)
        db.session.add_all([a, b])
        db.session.flush()
        trip = Trip(trip_name="Split", invite_code="SPL001", created_by=a.id)
        db.session.add(trip)
        db.session.flush()
        db.session.add_all([
            TripMember(trip_id=trip.id, user_id=a.id, role=ROLE_OWNER),
            TripMember(trip_id=trip.id, user_id=b.id, role=ROLE_MEMBER),
        ])
        db.session.commit()

        self._login("a3@qa.com")
        r = self.client.post(
            f"/groups/{trip.id}/expenses/add",
            data={
                "description": "Bad split",
                "amount_exact": "100.00",
                "split_method": "exact",
                "paid_by_user_id": str(a.id),
                "participant_user_ids": [str(a.id), str(b.id)],
                f"split_exact_{a.id}": "60",
                f"split_exact_{b.id}": "30",
            },
            follow_redirects=False,
        )
        self.assertIn(r.status_code, (302, 303))
        self.assertIsNone(Expense.query.filter_by(description="Bad split").first())

    def test_qa_expense_delete_not_implemented(self):
        """Document: no delete/archive expense route exists."""
        pw = bcrypt.generate_password_hash(self._password).decode("utf-8")
        user = User(name="U", email="u@qa.com", password=pw)
        db.session.add(user)
        db.session.flush()
        expense = Expense(paid_by=user.id, description="Old", amount=Decimal("10"))
        db.session.add(expense)
        db.session.commit()

        self._login("u@qa.com")
        for method, url in [
            ("POST", f"/expenses/{expense.id}/delete"),
            ("DELETE", f"/expenses/{expense.id}"),
            ("POST", f"/groups/1/expenses/{expense.id}/delete"),
        ]:
            r = self.client.open(url, method=method, follow_redirects=False)
            self.assertIn(r.status_code, (404, 405), msg=f"{method} {url}")

    # ── SECURITY ──

    def test_qa_notification_other_user_404(self):
        note = Notification(user_id=1, message="secret", kind="test")
        a = User(name="A", email="na@qa.com", password="x")
        b = User(name="B", email="nb@qa.com", password="x")
        db.session.add_all([a, b])
        db.session.flush()
        note.user_id = a.id
        db.session.add(note)
        db.session.commit()

        self._login("nb@qa.com")
        r = self.client.get(f"/notifications/{note.id}/go", follow_redirects=False)
        self.assertEqual(r.status_code, 404)

    def test_qa_invalid_expense_id_for_non_member(self):
        pw = bcrypt.generate_password_hash(self._password).decode("utf-8")
        owner = User(name="O", email="o3@qa.com", password=pw)
        outsider = User(name="X", email="x2@qa.com", password=pw)
        db.session.add_all([owner, outsider])
        db.session.flush()
        trip = Trip(trip_name="T", invite_code="T00001", created_by=owner.id)
        db.session.add(trip)
        db.session.flush()
        db.session.add(TripMember(trip_id=trip.id, user_id=owner.id, role=ROLE_OWNER))
        expense = Expense(trip_id=trip.id, paid_by=owner.id, description="Secret", amount=Decimal("50"))
        db.session.add(expense)
        db.session.commit()

        self._login("x2@qa.com")
        r = self.client.get(
            f"/groups/{trip.id}/expenses/{expense.id}",
            follow_redirects=False,
        )
        self.assertIn(r.status_code, (403, 404))

    def test_qa_receipt_invalid_file_rejected(self):
        self._register("Uploader", "up@qa.com")
        self._login("up@qa.com")
        r = self.client.post(
            "/expenses/scan-receipt",
            data={"receipt": (io.BytesIO(b"not-an-image"), "bad.txt")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertFalse(body.get("success", True))

    def test_qa_analytics_empty_and_with_data(self):
        pw = bcrypt.generate_password_hash(self._password).decode("utf-8")
        user = User(name="Ana", email="ana@qa.com", password=pw)
        db.session.add(user)
        db.session.commit()

        stats_empty = build_user_analytics(user.id, "30")
        self.assertEqual(stats_empty.expense_count, 0)

        self._login("ana@qa.com")
        r = self.client.get("/analytics?range=30")
        self.assertEqual(r.status_code, 200)

        expense = Expense(paid_by=user.id, description="Coffee", amount=Decimal("5"), category="Food")
        db.session.add(expense)
        db.session.commit()

        stats = build_user_analytics(user.id, "30")
        self.assertEqual(stats.expense_count, 1)
        self.assertGreater(float(stats.total_spending), 0)

    def test_qa_guest_pay_invalid_token(self):
        r = self.client.get("/pay/not-a-valid-token", follow_redirects=False)
        self.assertEqual(r.status_code, 404)

    def test_qa_csrf_not_enforced(self):
        """Document: app has no CSRF tokens on forms."""
        self._register("Csrf", "csrf@qa.com")
        with self.client.session_transaction() as sess:
            self.assertNotIn("csrf_token", sess)


if __name__ == "__main__":
    unittest.main()
