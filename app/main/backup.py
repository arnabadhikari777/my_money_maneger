"""
Encrypted JSON backup/restore.
The backup file is encrypted with a key derived (PBKDF2-HMAC-SHA256) from a
password the user provides at export/import time - never stored on disk.
Uses the well-established `cryptography` library (Fernet); no custom crypto.
"""
import json
import base64
from decimal import Decimal
from datetime import date, datetime

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.extensions import db
from app.models import User, Account, Category, Subcategory, Transaction, Budget, CashHolding

PBKDF2_ITERATIONS = 390_000
SALT_SIZE = 16


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


class _JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)


def export_user_data(user: User, password: str) -> bytes:
    data = {
        "version": 1,
        "user": {"username": user.username, "email": user.email},
        "accounts": [
            {"id": a.id, "name": a.name, "account_type": a.account_type, "last4": a.last4,
             "balance": a.balance, "archived": a.archived,
             "cash_holdings": [{"denom": h.denom, "count": h.count} for h in a.cash_holdings]}
            for a in user.accounts
        ],
        "categories": [
            {"id": c.id, "name": c.name,
             "subcategories": [{"id": s.id, "name": s.name} for s in c.subcategories]}
            for c in user.categories
        ],
        "transactions": [
            {"id": t.id, "type": t.type, "amount": t.amount, "account_id": t.account_id,
             "category_id": t.category_id, "subcategory_id": t.subcategory_id,
             "payment_method": t.payment_method, "date": t.date, "note": t.note,
             "cash_breakdown": t.cash_breakdown}
            for t in user.transactions
        ],
        "budgets": [{"period_key": b.period_key, "amount": b.amount} for b in user.budgets],
    }
    raw = json.dumps(data, cls=_JSONEncoder).encode("utf-8")

    salt = Fernet.generate_key()[:SALT_SIZE]  # 16 random bytes, reuse Fernet's CSPRNG
    key = _derive_key(password, salt)
    token = Fernet(key).encrypt(raw)
    return salt + b"::" + token  # salt is stored alongside the ciphertext, never the password


def import_user_data(user: User, password: str, blob: bytes):
    """Restores data for `user` from an encrypted backup blob.
    Validates the payload fully before touching the database (atomic import)."""
    try:
        salt, token = blob.split(b"::", 1)
    except ValueError:
        raise ValueError("This does not look like a valid backup file.")

    key = _derive_key(password, salt)
    try:
        raw = Fernet(key).decrypt(token)
    except InvalidToken:
        raise ValueError("Wrong password, or the backup file is corrupted.")

    data = json.loads(raw)
    if data.get("version") != 1:
        raise ValueError("Unsupported backup version.")

    # Wipe this user's existing data, then rebuild from the validated backup,
    # all inside one DB transaction so a failure leaves nothing half-written.
    # Bulk .delete() does not fire ORM cascades, so clear cash holdings explicitly.
    Transaction.query.filter_by(user_id=user.id).delete()
    Subcategory.query.filter(Subcategory.category_id.in_(
        db.session.query(Category.id).filter_by(user_id=user.id)
    )).delete(synchronize_session=False)
    Category.query.filter_by(user_id=user.id).delete()
    CashHolding.query.filter(CashHolding.account_id.in_(
        db.session.query(Account.id).filter_by(user_id=user.id)
    )).delete(synchronize_session=False)
    Account.query.filter_by(user_id=user.id).delete()
    Budget.query.filter_by(user_id=user.id).delete()
    db.session.flush()

    cat_id_map, sub_id_map, account_id_map = {}, {}, {}

    for a in data.get("accounts", []):
        acc = Account(user_id=user.id, name=a["name"], account_type=a["account_type"],
                      last4=a.get("last4"), balance=Decimal(a["balance"]), archived=a.get("archived", False))
        db.session.add(acc)
        db.session.flush()
        account_id_map[a["id"]] = acc.id
        for h in a.get("cash_holdings") or []:
            db.session.add(CashHolding(
                account_id=acc.id, denom=int(h["denom"]), count=max(0, int(h["count"]))
            ))

    for c in data.get("categories", []):
        cat = Category(user_id=user.id, name=c["name"])
        db.session.add(cat)
        db.session.flush()
        cat_id_map[c["id"]] = cat.id
        for s in c.get("subcategories", []):
            sub = Subcategory(category_id=cat.id, name=s["name"])
            db.session.add(sub)
            db.session.flush()
            sub_id_map[s["id"]] = sub.id

    for t in data.get("transactions", []):
        db.session.add(Transaction(
            user_id=user.id,
            type=t["type"],
            amount=Decimal(t["amount"]),
            account_id=account_id_map.get(t["account_id"]),
            category_id=cat_id_map.get(t["category_id"]) if t.get("category_id") else None,
            subcategory_id=sub_id_map.get(t["subcategory_id"]) if t.get("subcategory_id") else None,
            payment_method=t.get("payment_method"),
            date=datetime.fromisoformat(t["date"]).date() if t.get("date") else None,
            note=t.get("note"),
            cash_breakdown=t.get("cash_breakdown"),
        ))

    for b in data.get("budgets", []):
        db.session.add(Budget(user_id=user.id, period_key=b["period_key"], amount=Decimal(b["amount"])))

    db.session.commit()
