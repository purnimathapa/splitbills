"""Payment reminder emails for overdue guest balances."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from flask_mail import Message

from models import (
    PAYMENT_STATUS_PENDING,
    ExpensePaymentLink,
    PaymentReminderLog,
    db,
)
from payment_links import build_guest_payment_url

logger = logging.getLogger(__name__)


def is_link_due_for_reminder(
    link_created_at: datetime,
    last_reminder_sent_at: datetime | None,
    interval_days: int,
    *,
    now: datetime | None = None,
) -> bool:
    """True if unpaid long enough and not reminded within the interval."""
    now = now or datetime.utcnow()
    age_cutoff = now - timedelta(days=interval_days)
    if link_created_at > age_cutoff:
        return False
    if last_reminder_sent_at and last_reminder_sent_at > age_cutoff:
        return False
    return True


def find_links_due_for_reminder(interval_days: int) -> list[ExpensePaymentLink]:
    """Pending payment links old enough to remind, respecting the send cooldown."""
    now = datetime.utcnow()
    age_cutoff = now - timedelta(days=interval_days)

    pending_links = ExpensePaymentLink.query.filter(
        ExpensePaymentLink.status == PAYMENT_STATUS_PENDING,
        ExpensePaymentLink.created_at <= age_cutoff,
    ).all()

    due: list[ExpensePaymentLink] = []
    for link in pending_links:
        last_sent = (
            PaymentReminderLog.query.filter_by(payment_link_id=link.id)
            .order_by(PaymentReminderLog.sent_at.desc())
            .first()
        )
        last_sent_at = last_sent.sent_at if last_sent else None
        if is_link_due_for_reminder(
            link.created_at,
            last_sent_at,
            interval_days,
            now=now,
        ):
            due.append(link)
    return due


def send_payment_reminder_email(
    mail,
    link: ExpensePaymentLink,
    *,
    secret_key: str,
    default_currency: str,
    default_sender,
) -> bool:
    """Send one reminder email. Returns True if sent."""
    guest = link.user
    expense = link.expense
    if guest is None or not guest.email:
        logger.warning("Skipping reminder for payment link %s: no guest email", link.id)
        return False

    pay_url = build_guest_payment_url(link, secret_key)
    trip_name = ""
    if expense and expense.trip_id:
        from models import Trip

        trip = Trip.query.get(expense.trip_id)
        trip_name = trip.trip_name if trip else ""

    description = expense.description if expense else "a shared expense"
    amount = link.amount_owed

    subject = f"Reminder: pay your share — {description}"
    body = (
        f"Hi {guest.name},\n\n"
        f"This is a friendly reminder that you still owe {default_currency} {amount:.2f} "
        f'for "{description}"'
        f"{f' ({trip_name})' if trip_name else ''}.\n\n"
        f"Pay securely here:\n{pay_url}\n\n"
        f"If you already paid, you can ignore this message.\n\n"
        f"— Split Bills\n"
    )

    message = Message(
        subject=subject,
        recipients=[guest.email],
        body=body,
        sender=default_sender,
    )
    mail.send(message)
    return True


def run_payment_reminder_job(app, mail) -> dict:
    """Find due links and send reminders. Called by the daily scheduler."""
    interval_days = app.config["REMINDER_INTERVAL_DAYS"]
    stats = {"due": 0, "sent": 0, "skipped": 0, "errors": 0}

    with app.app_context():
        due_links = find_links_due_for_reminder(interval_days)
        stats["due"] = len(due_links)

        for link in due_links:
            try:
                sent = send_payment_reminder_email(
                    mail,
                    link,
                    secret_key=app.config["SECRET_KEY"],
                    default_currency=app.config.get("DEFAULT_CURRENCY", "Rs"),
                    default_sender=app.config["MAIL_DEFAULT_SENDER"],
                )
                if not sent:
                    stats["skipped"] += 1
                    continue

                db.session.add(
                    PaymentReminderLog(
                        payment_link_id=link.id,
                        email_to=link.user.email,
                    )
                )
                expense = link.expense
                guest = link.user
                if expense and expense.paid_by and guest:
                    from models import NOTIFICATION_REMINDER_SENT
                    from notifications import create_notification

                    create_notification(
                        expense.paid_by,
                        f'Reminder sent to {guest.name} for "{expense.description or "a shared expense"}"',
                        kind=NOTIFICATION_REMINDER_SENT,
                        href="/collect",
                    )
                db.session.commit()
                stats["sent"] += 1
                logger.info(
                    "Payment reminder sent link_id=%s to=%s",
                    link.id,
                    link.user.email,
                )
            except Exception:
                db.session.rollback()
                stats["errors"] += 1
                logger.exception("Failed payment reminder for link_id=%s", link.id)

    return stats
