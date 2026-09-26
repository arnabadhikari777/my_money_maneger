import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-to-a-long-random-value-before-deploying")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "instance", "moneymanager.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Sessions / cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Set SESSION_COOKIE_SECURE = True once served over HTTPS (PythonAnywhere free domains are HTTPS)
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") == "1"
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    # WTForms / CSRF
    WTF_CSRF_ENABLED = True

    # Web Push (VAPID) - generate your own keys before enabling push notifications.
    # See DEPLOY.md for how to generate these with the `pywebpush` / `py-vapid` tools.
    VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
    VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
    VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:you@example.com")

    # Backup encryption uses the user's own password-derived key (see backup.py), no separate secret needed.

    DEFAULT_CATEGORIES = {
        "Food": ["Breakfast", "Lunch", "Dinner", "Restaurant", "Snacks"],
        "Transport": ["Bus", "Train", "Auto", "Taxi", "Fuel"],
        "Shopping": ["Clothing", "Groceries", "Electronics", "Other"],
        "Education": ["Fees", "Books", "Courses"],
        "Health": ["Medicine", "Doctor", "Fitness"],
        "Technology": ["Gadgets", "Software", "Repairs"],
        "Home": ["Rent", "Utilities", "Maintenance"],
        "Entertainment": ["Movies", "Subscriptions", "Outings"],
        "Other": ["Miscellaneous"],
    }

    PAYMENT_METHODS = ["Cash", "UPI", "Debit Card", "Credit Card", "Bank Transfer", "Other"]
    ACCOUNT_TYPES = ["Bank Account", "Cash", "UPI", "Debit Card", "Credit Card", "Other"]
    CASH_DENOMINATIONS = [500, 200, 100, 50, 20, 10, 5, 2, 1]

    # Recharge/subscription validity dropdown (days, label). Reminders fire
    # 7 days and 3 days before the computed expiry date - not configurable
    # per item, this applies the same way everywhere.
    RECHARGE_DURATIONS = [
        (1, "1 day"),
        (7, "7 days (weekly)"),
        (28, "28 days"),
        (30, "30 days"),
        (56, "56 days"),
        (84, "84 days"),
        (180, "180 days (6 months)"),
        (210, "210 days (7 months)"),
        (365, "365 days (yearly)"),
    ]
