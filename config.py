import os
from dotenv import load_dotenv

load_dotenv()


class Config:

    SECRET_KEY = os.getenv(
        "SECRET_KEY",
        "splitbills_secret_key_2026"
    )

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://root:purnima123@localhost/splitbills"
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = "static/uploads"
    # Receipt images are stored under Flask static_folder (style/receipts/)
    RECEIPT_UPLOAD_SUBDIR = "receipts"
    RECEIPT_MAX_BYTES = 5 * 1024 * 1024

    # Receipt OCR (Tesseract) — optional; itemized expenses work without it
    RECEIPT_OCR_ENABLED = os.getenv("RECEIPT_OCR_ENABLED", "true").lower() == "true"
    # Set if tesseract is not on PATH (common on Windows), e.g. C:/Program Files/Tesseract-OCR/tesseract.exe
    TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")
    DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "Rs")
    EXCHANGE_API_BASE = os.getenv("EXCHANGE_API_BASE", "https://api.exchangerate.host")

    # --- Payment reminders ---
    # Unpaid guest links must be at least this many days old before emailing,
    # and we won't send another reminder until this many days after the last one.
    REMINDER_INTERVAL_DAYS = int(os.getenv("REMINDER_INTERVAL_DAYS", "3"))
    REMINDER_JOB_ENABLED = os.getenv("REMINDER_JOB_ENABLED", "true").lower() == "true"
    REMINDER_JOB_HOUR = int(os.getenv("REMINDER_JOB_HOUR", "9"))  # UTC, daily cron

    # --- Recurring expenses ---
    RECURRING_JOB_ENABLED = os.getenv("RECURRING_JOB_ENABLED", "true").lower() == "true"
    RECURRING_JOB_HOUR = int(os.getenv("RECURRING_JOB_HOUR", "1"))  # UTC, daily cron

    # --- Flask-Mail (reminders) ---
    MAIL_SERVER = os.getenv("MAIL_SERVER", "localhost")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "1025"))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "false").lower() == "true"
    MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "false").lower() == "true"
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "noreply@splitbills.local")

    # --- Khalti ePayment (https://docs.khalti.com/khalti-epayment/) ---
    # Sandbox: create a test merchant at https://test-admin.khalti.com
    #   KHALTI_SECRET_KEY=test_secret_key_xxxxxxxx
    #   KHALTI_PUBLIC_KEY=test_public_key_xxxxxxxx
    # Production: swap to live keys from https://admin.khalti.com
    #   KHALTI_SECRET_KEY=live_secret_key_xxxxxxxx
    #   KHALTI_PUBLIC_KEY=live_public_key_xxxxxxxx
    # The API host stays https://khalti.com/api/v2; only the key prefix changes.
    KHALTI_PUBLIC_KEY = os.getenv("KHALTI_PUBLIC_KEY", "")
    KHALTI_SECRET_KEY = os.getenv("KHALTI_SECRET_KEY", "")
    KHALTI_WEBHOOK_SECRET = os.getenv("KHALTI_WEBHOOK_SECRET", "")
    # Local dev only: simulate Khalti checkout on 127.0.0.1 when no secret key is set
    KHALTI_DEV_MODE = os.getenv("KHALTI_DEV_MODE", "false").lower() == "true"

    # --- Stripe Checkout (optional guest card payments) ---
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    # ISO code for Stripe (npr, usd, …). DEFAULT_CURRENCY "Rs" maps to npr in stripe_pay.py
    STRIPE_CURRENCY = os.getenv("STRIPE_CURRENCY", "npr")

    # Pairwise balance at or below this amount (same currency units) shows settle-up nudge
    SETTLE_SUGGESTION_THRESHOLD = float(os.getenv("SETTLE_SUGGESTION_THRESHOLD", "200"))
