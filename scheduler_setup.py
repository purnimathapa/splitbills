"""APScheduler jobs for Split Bills (runs in-process with Flask)."""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(daemon=True)


def start_reminder_scheduler(app, mail) -> None:
    """Register daily reminder job. Safe to call once at app startup."""
    if not app.config.get("REMINDER_JOB_ENABLED", True):
        logger.info("Payment reminder scheduler disabled (REMINDER_JOB_ENABLED=false)")
    else:
        hour = app.config.get("REMINDER_JOB_HOUR", 9)

        def _reminder_job():
            from reminders import run_payment_reminder_job

            stats = run_payment_reminder_job(app, mail)
            logger.info("Payment reminder job finished: %s", stats)

        scheduler.add_job(
            _reminder_job,
            trigger="cron",
            hour=hour,
            minute=0,
            id="payment_reminder_daily",
            replace_existing=True,
        )
        logger.info("Scheduled payment reminders daily at %s:00 UTC", hour)

    if not app.config.get("RECURRING_JOB_ENABLED", True):
        logger.info("Recurring expense scheduler disabled (RECURRING_JOB_ENABLED=false)")
    else:
        recurring_hour = app.config.get("RECURRING_JOB_HOUR", 1)

        def _recurring_job():
            from recurring_expenses import run_recurring_expense_job

            stats = run_recurring_expense_job(app)
            logger.info("Recurring expense job finished: %s", stats)

        scheduler.add_job(
            _recurring_job,
            trigger="cron",
            hour=recurring_hour,
            minute=5,
            id="recurring_expense_daily",
            replace_existing=True,
        )
        logger.info("Scheduled recurring expenses daily at %s:05 UTC", recurring_hour)

    if scheduler.get_jobs() and not scheduler.running:
        scheduler.start()
        logger.info("APScheduler started")


def init_scheduler_for_app(app, mail) -> None:
    """Avoid double-start when Flask debug reloader spawns two processes."""
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    start_reminder_scheduler(app, mail)
