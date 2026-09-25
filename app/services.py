"""
Core financial logic: every balance change flows through these functions so
that account balances, category totals, budgets, and cash-note inventories
can never drift out of sync with the transaction log. All functions run
inside a single DB transaction (atomic commit/rollback).

Cash denomination tracking
---------------------------
A transaction's `cash_breakdown` column (JSON text) can describe how physical
notes were involved:

  Add Money (income) with counted cash:
      {"denoms": {"500": 2, "100": 1}}
      -> those notes are ADDED to the account's note inventory.

  Expense paid in cash, tracking notes given + change received:
      {"given": {"500": 1}, "change": {"100": 2, "50": 1}}
      -> "given" notes are REMOVED from inventory, "change" notes are ADDED.
      -> amount = sum(given) - sum(change)

Reversing a transaction (edit/delete) applies the exact opposite deltas.
"""
import json
from decimal import Decimal
from app.extensions import db
from app.models import Account, Transaction, CashHolding, Recharge


def _apply_effect(account: Account, txn_type: str, amount: Decimal, sign: int = 1):
    """sign=1 applies the transaction's effect, sign=-1 reverses it."""
    delta = Decimal(amount) * sign
    if txn_type == "income":
        account.balance = Decimal(account.balance) + delta
    else:  # expense
        account.balance = Decimal(account.balance) - delta


def _adjust_holding(account_id, denom, delta):
    if delta == 0:
        return
    denom = int(denom)
    holding = CashHolding.query.filter_by(account_id=account_id, denom=denom).first()
    if holding is None:
        holding = CashHolding(account_id=account_id, denom=denom, count=0)
        db.session.add(holding)
    holding.count = max(0, holding.count + delta)


def _apply_cash_breakdown(account_id, breakdown_json, sign: int = 1):
    """Applies (sign=1) or reverses (sign=-1) a transaction's note-level effect
    on the account's cash inventory. Safe no-op if there's no breakdown."""
    if not breakdown_json:
        return
    try:
        data = json.loads(breakdown_json)
    except (ValueError, TypeError):
        return

    if "denoms" in data:  # Add Money counted in cash -> notes come IN
        for denom, qty in data["denoms"].items():
            _adjust_holding(account_id, denom, sign * int(qty))

    if "given" in data or "change" in data:  # cash expense -> notes go OUT, change comes IN
        for denom, qty in (data.get("given") or {}).items():
            _adjust_holding(account_id, denom, -sign * int(qty))
        for denom, qty in (data.get("change") or {}).items():
            _adjust_holding(account_id, denom, sign * int(qty))


def cash_breakdown_amount(breakdown: dict) -> Decimal:
    """Given a parsed breakdown dict, compute the net amount it represents."""
    if "denoms" in breakdown:
        return sum((Decimal(d) * int(q) for d, q in breakdown["denoms"].items()), Decimal("0"))
    if "given" in breakdown or "change" in breakdown:
        given = sum((Decimal(d) * int(q) for d, q in (breakdown.get("given") or {}).items()), Decimal("0"))
        change = sum((Decimal(d) * int(q) for d, q in (breakdown.get("change") or {}).items()), Decimal("0"))
        return given - change
    return Decimal("0")


def create_transaction(user_id, txn_type, amount, account_id, category_id=None,
                        subcategory_id=None, payment_method=None, date=None,
                        note=None, cash_breakdown=None):
    account = Account.query.filter_by(id=account_id, user_id=user_id).first()
    if account is None:
        raise ValueError("Account not found")

    txn = Transaction(
        user_id=user_id,
        type=txn_type,
        amount=Decimal(amount),
        account_id=account_id,
        category_id=category_id,
        subcategory_id=subcategory_id,
        payment_method=payment_method,
        date=date,
        note=note,
        cash_breakdown=cash_breakdown,
    )
    _apply_effect(account, txn_type, txn.amount, sign=1)
    _apply_cash_breakdown(account_id, cash_breakdown, sign=1)

    db.session.add(txn)
    db.session.add(account)
    db.session.commit()
    return txn


def update_transaction(user_id, txn_id, **fields):
    txn = Transaction.query.filter_by(id=txn_id, user_id=user_id).first()
    if txn is None:
        raise ValueError("Transaction not found")

    old_account = Account.query.filter_by(id=txn.account_id, user_id=user_id).first()
    _apply_effect(old_account, txn.type, txn.amount, sign=-1)  # reverse old effect
    _apply_cash_breakdown(txn.account_id, txn.cash_breakdown, sign=-1)  # reverse old notes

    new_account_id = fields.get("account_id", txn.account_id)
    new_type = fields.get("type", txn.type)
    new_amount = Decimal(fields.get("amount", txn.amount))
    new_cash_breakdown = fields.get("cash_breakdown", txn.cash_breakdown)

    new_account = old_account
    if new_account_id != txn.account_id:
        new_account = Account.query.filter_by(id=new_account_id, user_id=user_id).first()
        if new_account is None:
            raise ValueError("Account not found")

    for key in ("type", "amount", "account_id", "category_id", "subcategory_id",
                "payment_method", "date", "note", "cash_breakdown"):
        if key in fields:
            setattr(txn, key, fields[key])
    txn.amount = new_amount

    _apply_effect(new_account, new_type, new_amount, sign=1)  # apply new effect
    _apply_cash_breakdown(new_account_id, new_cash_breakdown, sign=1)  # apply new notes

    db.session.add(txn)
    db.session.add(old_account)
    if new_account is not old_account:
        db.session.add(new_account)
    db.session.commit()
    return txn


def delete_transaction(user_id, txn_id):
    txn = Transaction.query.filter_by(id=txn_id, user_id=user_id).first()
    if txn is None:
        raise ValueError("Transaction not found")

    account = Account.query.filter_by(id=txn.account_id, user_id=user_id).first()
    if account is not None:
        _apply_effect(account, txn.type, txn.amount, sign=-1)
        _apply_cash_breakdown(txn.account_id, txn.cash_breakdown, sign=-1)
        db.session.add(account)

    Recharge.query.filter_by(transaction_id=txn.id).delete()
    db.session.delete(txn)
    db.session.commit()


def set_cash_holdings(account_id, counts: dict):
    """Manually set (not adjust) the note inventory for an account -
    used for one-time initial setup ('I currently have these notes in hand')."""
    for denom, count in counts.items():
        denom = int(denom)
        count = max(0, int(count))
        holding = CashHolding.query.filter_by(account_id=account_id, denom=denom).first()
        if holding is None:
            holding = CashHolding(account_id=account_id, denom=denom, count=count)
            db.session.add(holding)
        else:
            holding.count = count
    db.session.commit()
