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
    # Currency / exchange-rate options
    DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "Rs")
    # If you want to enable live rates, set EXCHANGE_API_BASE to an API that does not require auth (e.g. https://api.exchangerate.host)
    EXCHANGE_API_BASE = os.getenv("EXCHANGE_API_BASE", "https://api.exchangerate.host")