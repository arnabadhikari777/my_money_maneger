from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    enc_salt = db.Column(db.String(32), nullable=True)  # for deriving the per-user note-encryption key
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    accounts = db.relationship("Account", backref="user", lazy=True, cascade="all, delete-orphan")
    categories = db.relationship("Category", backref="user", lazy=True, cascade="all, delete-orphan")
    transactions = db.relationship("Transaction", backref="user", lazy=True, cascade="all, delete-orphan")
    budgets = db.relationship("Budget", backref="user", lazy=True, cascade="all, delete-orphan")
    subscriptions = db.relationship("PushSubscription", backref="user", lazy=True, cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Account(db.Model):
    __tablename__ = "accounts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    account_type = db.Column(db.String(30), nullable=False, default="Bank Account")
    last4 = db.Column(db.String(4), nullable=True)  # never store full card/account numbers
    balance = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    archived = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    transactions = db.relationship("Transaction", backref="account", lazy=True)
    cash_holdings = db.relationship("CashHolding", backref="account", lazy=True, cascade="all, delete-orphan")

    def display_name(self):
        return f"{self.name} •{self.last4}" if self.last4 else self.name


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)

    subcategories = db.relationship("Subcategory", backref="category", lazy=True, cascade="all, delete-orphan")
    transactions = db.relationship("Transaction", backref="category", lazy=True)

    __table_args__ = (db.UniqueConstraint("user_id", "name", name="uq_category_user_name"),)


class Subcategory(db.Model):
    __tablename__ = "subcategories"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)

    transactions = db.relationship("Transaction", backref="subcategory", lazy=True)


class Transaction(db.Model):
    """A single money movement: either 'income' (Add Money) or 'expense'."""
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    type = db.Column(db.String(10), nullable=False)  # 'income' | 'expense'
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True, index=True)
    subcategory_id = db.Column(db.Integer, db.ForeignKey("subcategories.id"), nullable=True)
    payment_method = db.Column(db.String(30), nullable=True)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    note = db.Column(db.String(255), nullable=True)
    cash_breakdown = db.Column(db.Text, nullable=True)  # JSON string, e.g. Add Money via cash denominations
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Budget(db.Model):
    """One budget per user per month (period_key = 'YYYY-MM')."""
    __tablename__ = "budgets"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    period_key = db.Column(db.String(7), nullable=False)  # '2026-09'
    amount = db.Column(db.Numeric(12, 2), nullable=False)

    __table_args__ = (db.UniqueConstraint("user_id", "period_key", name="uq_budget_user_period"),)


class CashHolding(db.Model):
    """How many notes of each denomination a Cash-type account currently holds.
    Updated automatically whenever a transaction records a denomination
    breakdown (Add Money via cash, or an expense paid with cash where notes
    given / change received are tracked)."""
    __tablename__ = "cash_holdings"

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False, index=True)
    denom = db.Column(db.Integer, nullable=False)
    count = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (db.UniqueConstraint("account_id", "denom", name="uq_cashholding_account_denom"),)


class Recharge(db.Model):
    """Tracks a recharge/subscription's validity period, linked to the expense
    that paid for it. Reminders fire 7 days and 3 days before `expiry_date`
    (checked once a day by the /api/pending-reminders endpoint); once expired,
    it shows as a red badge on the dashboard until dismissed."""
    __tablename__ = "recharges"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=True, index=True)
    duration_days = db.Column(db.Integer, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    expiry_date = db.Column(db.Date, nullable=False, index=True)
    notified_7d = db.Column(db.Boolean, nullable=False, default=False)
    notified_3d = db.Column(db.Boolean, nullable=False, default=False)
    dismissed = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    transaction = db.relationship("Transaction", backref=db.backref("recharge", uselist=False))


class PushSubscription(db.Model):
    __tablename__ = "push_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    endpoint = db.Column(db.String(500), nullable=False, unique=True)
    p256dh = db.Column(db.String(255), nullable=False)
    auth = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
