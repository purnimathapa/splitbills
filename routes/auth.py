from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from models import User, db
from notifications import mark_all_read, mark_read


def register(app, bcrypt):
    def _password_matches(stored_hash: str, password: str) -> bool:
        try:
            return bcrypt.check_password_hash(stored_hash, password)
        except (ValueError, TypeError):
            return False

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
                return render_template(
                    "register.html",
                    error="An account with that email already exists.",
                    name=name,
                    email=email,
                )

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

            if user and _password_matches(user.password, password):
                login_user(user)
                return redirect(url_for("dashboard"))

            return render_template(
                "login.html",
                error="Invalid email or password.",
                email=email,
            )

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
        if not mark_read(notification_id, current_user.id):
            abort(404)
        db.session.commit()
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/notifications/<int:notification_id>/go")
    @login_required
    def notification_go(notification_id):
        from datetime import datetime

        from notifications import get_notification_for_user

        note = get_notification_for_user(notification_id, current_user.id)
        if note is None:
            abort(404)
        if note.read_at is None:
            note.read_at = datetime.utcnow()
            db.session.commit()
        return redirect(note.href or url_for("dashboard"))

    @app.route("/profile")
    @login_required
    def profile():
        return render_template("profile.html")
