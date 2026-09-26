from datetime import date, datetime, timedelta
from decimal import Decimal
import json

from flask import (Blueprint, render_template, redirect, url_for, flash,
                    request, jsonify, send_file, abort, session)
from flask_login import login_required, current_user
import io

from app.extensions import db
from app.models import Account, Category, Subcategory, Transaction, Budget, PushSubscription, CashHolding, Recharge
from app import services
from app.crypto import encrypt_text, decrypt_text
from app.main.forms import (AccountForm, AddMoneyForm, ExpenseForm, BudgetForm,
                             CategoryForm, SubcategoryForm, RestoreForm)
from app.main.utils import date_range_for, month_key, month_label, last_n_months
from app.main.backup import export_user_data, import_user_data
from config import Config

main_bp = Blueprint("main", __name__)


def _enc_key():
    key_str = session.get("enc_key")
    return key_str.encode("utf-8") if key_str else None


def _require_unlock():
    """Call at the top of any route that reads/writes a note in plaintext.
    Returns a redirect response if the session doesn't have the encryption
    key yet (e.g. after a 'Remember me' auto-login); otherwise None."""
    if _enc_key() is None:
        flash("Enter your password to unlock your notes first.", "info")
        return redirect(url_for("auth.unlock", next=request.path))
    return None


def _encrypt_note(raw):
    key = _enc_key()
    if key is None or not raw:
        return None
    return encrypt_text(key, raw)


def _decrypt_note(token):
    key = _enc_key()
    if key is None or not token:
        return None
    return decrypt_text(key, token)


# ---------- helpers ----------

def _user_accounts():
    return Account.query.filter_by(user_id=current_user.id, archived=False).order_by(Account.name).all()


def _user_categories():
    return Category.query.filter_by(user_id=current_user.id).order_by(Category.name).all()


def _populate_account_choices(field):
    field.choices = [(a.id, a.display_name()) for a in _user_accounts()]


def _populate_category_choices(field):
    field.choices = [(c.id, c.name) for c in _user_categories()]


def _read_denom_dict(prefix):
    """Reads posted `<prefix>_<denom>` quantity fields into {denom_str: qty}, qty>0 only."""
    result = {}
    for d in Config.CASH_DENOMINATIONS:
        raw = request.form.get(f"{prefix}_{d}", "0")
        try:
            qty = int(raw)
        except ValueError:
            qty = 0
        if qty > 0:
            result[str(d)] = qty
    return result


def _income_cash_from_request():
    """Add Money page: counted notes -> (amount, cash_breakdown_json) or (None, None)."""
    denoms = _read_denom_dict("denom")
    if not denoms:
        return None, None
    total = sum(int(d) * q for d, q in denoms.items())
    return Decimal(total), json.dumps({"denoms": denoms})


def _expense_cash_from_request():
    """Add Expense page paid in cash: notes given + change received ->
    (amount, cash_breakdown_json) or (None, None) if nothing was entered."""
    given = _read_denom_dict("given")
    change = _read_denom_dict("change")
    if not given and not change:
        return None, None
    given_total = sum(int(d) * q for d, q in given.items())
    change_total = sum(int(d) * q for d, q in change.items())
    amount = Decimal(given_total - change_total)
    return amount, json.dumps({"given": given, "change": change})


def _validate_cash_given(account_id, given: dict, old_given: dict = None):
    """Returns a list of (denom, requested, available) for any denomination
    where more notes were 'given' than the wallet actually holds. `old_given`
    (when editing a transaction) is added back to availability, since editing
    first reverses the transaction's old effect before applying the new one."""
    holding_map = {h.denom: h.count for h in CashHolding.query.filter_by(account_id=account_id).all()}
    old_given = old_given or {}
    problems = []
    for denom_str, qty in given.items():
        denom = int(denom_str)
        available = holding_map.get(denom, 0) + int(old_given.get(denom_str, 0))
        if qty > available:
            problems.append((denom, qty, available))
    return problems


def _cash_shortage_message(problems):
    parts = [f"₹{d}: asked for {q}, you have {a}" for d, q, a in problems]
    return "You can't give more notes than you actually have — " + "; ".join(parts)


def _recharge_label(recharge):
    txn = recharge.transaction
    if txn is None:
        return "Recharge"
    if txn.category and txn.subcategory:
        return f"{txn.category.name} · {txn.subcategory.name}"
    if txn.category:
        return txn.category.name
    return "Recharge"


def _totals_for_range(start, end):
    txns = Transaction.query.filter(
        Transaction.user_id == current_user.id,
        Transaction.date >= start,
        Transaction.date <= end,
    ).all()
    income = sum((t.amount for t in txns if t.type == "income"), Decimal("0"))
    expense = sum((t.amount for t in txns if t.type == "expense"), Decimal("0"))
    return txns, income, expense


# ---------- dashboard ----------

@main_bp.route("/")
@login_required
def dashboard():
    accounts = _user_accounts()
    total_available = sum((a.balance for a in accounts), Decimal("0"))

    cash_accounts = [a for a in accounts if a.account_type == "Cash"]
    other_accounts = [a for a in accounts if a.account_type != "Cash"]
    cash_total = sum((a.balance for a in cash_accounts), Decimal("0"))
    others_total = sum((a.balance for a in other_accounts), Decimal("0"))

    combined_holdings = {}
    for a in cash_accounts:
        for h in a.cash_holdings:
            if h.count:
                combined_holdings[h.denom] = combined_holdings.get(h.denom, 0) + h.count
    cash_notes = sorted(combined_holdings.items(), key=lambda kv: kv[0], reverse=True)

    today = date.today()
    month_start = today.replace(day=1)
    _, month_income, month_expense = _totals_for_range(month_start, today)

    recent = (Transaction.query.filter_by(user_id=current_user.id)
              .order_by(Transaction.date.desc(), Transaction.created_at.desc())
              .limit(8).all())

    budget = Budget.query.filter_by(user_id=current_user.id, period_key=month_key(today)).first()

    recharges = Recharge.query.filter_by(user_id=current_user.id, dismissed=False).all()
    expired_recharges = []
    upcoming_recharges = []
    for r in recharges:
        r.label = _recharge_label(r)
        r.days_left = (r.expiry_date - today).days
        if r.expiry_date < today:
            expired_recharges.append(r)
        elif r.days_left <= 7:
            upcoming_recharges.append(r)
    expired_recharges.sort(key=lambda r: r.expiry_date)
    upcoming_recharges.sort(key=lambda r: r.expiry_date)

    return render_template(
        "dashboard.html",
        accounts=accounts,
        total_available=total_available,
        cash_accounts=cash_accounts,
        cash_total=cash_total,
        cash_notes=cash_notes,
        others_total=others_total,
        other_accounts_count=len(other_accounts),
        month_expense=month_expense,
        month_income=month_income,
        remaining=total_available,
        recent=recent,
        budget=budget,
        expired_recharges=expired_recharges,
        upcoming_recharges=upcoming_recharges,
    )


# ---------- accounts ----------

@main_bp.route("/accounts")
@login_required
def accounts_list():
    accounts = _user_accounts()
    total = sum((a.balance for a in accounts), Decimal("0"))
    return render_template("accounts/list.html", accounts=accounts, total=total)


@main_bp.route("/accounts/add", methods=["GET", "POST"])
@login_required
def account_add():
    form = AccountForm()
    if form.validate_on_submit():
        acc = Account(
            user_id=current_user.id,
            name=form.name.data.strip(),
            account_type=form.account_type.data,
            last4=form.last4.data or None,
            balance=form.opening_balance.data,
        )
        db.session.add(acc)
        db.session.commit()
        flash(f"Account '{acc.name}' created.", "success")
        return redirect(url_for("main.accounts_list"))
    return render_template("accounts/form.html", form=form, title="Add Account")


@main_bp.route("/accounts/<int:account_id>")
@login_required
def account_detail(account_id):
    account = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    q = request.args.get("q", "").strip()
    query = Transaction.query.filter_by(user_id=current_user.id, account_id=account.id)
    txns = query.order_by(Transaction.date.desc()).all()
    if q:
        # Notes are encrypted, so matching has to happen after decrypting in Python.
        q_lower = q.lower()
        txns = [t for t in txns if q_lower in (_decrypt_note(t.note) or "").lower()]
    txns = txns[:200]
    return render_template("accounts/detail.html", account=account, txns=txns, q=q)


@main_bp.route("/accounts/<int:account_id>/cash-holdings.json")
@login_required
def cash_holdings_json(account_id):
    account = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    if account.account_type != "Cash":
        return jsonify({"is_cash": False, "holdings": {}})
    existing = {h.denom: h.count for h in account.cash_holdings}
    holdings = {str(d): existing.get(d, 0) for d in Config.CASH_DENOMINATIONS}
    return jsonify({"is_cash": True, "holdings": holdings})


@main_bp.route("/accounts/<int:account_id>/cash", methods=["GET", "POST"])
@login_required
def cash_holdings(account_id):
    account = Account.query.filter_by(id=account_id, user_id=current_user.id, account_type="Cash").first_or_404()

    if request.method == "POST":
        counts = {d: request.form.get(f"count_{d}", "0") for d in Config.CASH_DENOMINATIONS}
        services.set_cash_holdings(account.id, counts)
        flash("Cash inventory updated.", "success")
        return redirect(url_for("main.account_detail", account_id=account.id))

    existing = {h.denom: h.count for h in account.cash_holdings}
    holdings = [(d, existing.get(d, 0)) for d in Config.CASH_DENOMINATIONS]
    total = sum(d * c for d, c in holdings)
    return render_template("accounts/cash_holdings.html", account=account, holdings=holdings, total=total)


@main_bp.route("/accounts/<int:account_id>/edit", methods=["GET", "POST"])
@login_required
def account_edit(account_id):
    account = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    form = AccountForm(obj=account)
    form.opening_balance.label.text = "Balance"
    if request.method == "GET":
        form.opening_balance.data = account.balance
    if form.validate_on_submit():
        account.name = form.name.data.strip()
        account.account_type = form.account_type.data
        account.last4 = form.last4.data or None
        account.balance = form.opening_balance.data
        db.session.commit()
        flash("Account updated.", "success")
        return redirect(url_for("main.accounts_list"))
    return render_template("accounts/form.html", form=form, title="Edit Account")


@main_bp.route("/accounts/<int:account_id>/delete", methods=["POST"])
@login_required
def account_delete(account_id):
    account = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    account.archived = True
    db.session.commit()
    flash("Account archived.", "info")
    return redirect(url_for("main.accounts_list"))


# ---------- categories ----------

@main_bp.route("/categories")
@login_required
def categories_list():
    categories = _user_categories()
    today = date.today()
    month_start = today.replace(day=1)
    totals = {}
    for c in categories:
        spent = (db.session.query(db.func.coalesce(db.func.sum(Transaction.amount), 0))
                 .filter(Transaction.user_id == current_user.id, Transaction.category_id == c.id,
                         Transaction.type == "expense", Transaction.date >= month_start, Transaction.date <= today)
                 .scalar())
        totals[c.id] = spent
    return render_template("categories/list.html", categories=categories, totals=totals)


@main_bp.route("/categories/add", methods=["GET", "POST"])
@login_required
def category_add():
    form = CategoryForm()
    if form.validate_on_submit():
        if Category.query.filter_by(user_id=current_user.id, name=form.name.data.strip()).first():
            flash("You already have a category with that name.", "error")
        else:
            db.session.add(Category(user_id=current_user.id, name=form.name.data.strip()))
            db.session.commit()
            flash("Category added.", "success")
            return redirect(url_for("main.categories_list"))
    return render_template("categories/category_form.html", form=form)


@main_bp.route("/categories/<int:category_id>", methods=["GET", "POST"])
@login_required
def category_detail(category_id):
    category = Category.query.filter_by(id=category_id, user_id=current_user.id).first_or_404()
    sub_form = SubcategoryForm()
    if sub_form.validate_on_submit():
        db.session.add(Subcategory(category_id=category.id, name=sub_form.name.data.strip()))
        db.session.commit()
        flash("Purpose added.", "success")
        return redirect(url_for("main.category_detail", category_id=category.id))

    q = request.args.get("q", "").strip()
    query = Transaction.query.filter_by(user_id=current_user.id, category_id=category.id, type="expense")
    txns = query.order_by(Transaction.date.desc()).all()
    if q:
        q_lower = q.lower()
        txns = [t for t in txns if q_lower in (_decrypt_note(t.note) or "").lower()]
    txns = txns[:200]
    total = sum((t.amount for t in txns), Decimal("0"))
    return render_template("categories/detail.html", category=category, txns=txns, total=total,
                            sub_form=sub_form, q=q)


@main_bp.route("/categories/<int:category_id>/delete", methods=["POST"])
@login_required
def category_delete(category_id):
    category = Category.query.filter_by(id=category_id, user_id=current_user.id).first_or_404()
    if Transaction.query.filter_by(category_id=category.id).first():
        flash("Can't delete a category that has expenses. Remove those expenses first.", "error")
    else:
        db.session.delete(category)
        db.session.commit()
        flash("Category deleted.", "info")
    return redirect(url_for("main.categories_list"))


# ---------- add money ----------

@main_bp.route("/add-money", methods=["GET", "POST"])
@login_required
def add_money():
    unlock_redirect = _require_unlock()
    if unlock_redirect:
        return unlock_redirect

    form = AddMoneyForm()
    _populate_account_choices(form.account_id)
    account_types = {a.id: a.account_type for a in _user_accounts()}
    if request.method == "GET":
        form.date.data = date.today()

    if not form.account_id.choices:
        flash("Create an account first before adding money.", "error")
        return redirect(url_for("main.account_add"))

    if form.validate_on_submit():
        account = Account.query.filter_by(id=form.account_id.data, user_id=current_user.id).first()

        if account and account.account_type == "Cash":
            # For a Cash wallet, the note count IS the amount - no manual entry.
            denom_amount, cash_breakdown = _income_cash_from_request()
            if denom_amount is None or denom_amount <= 0:
                flash("Pick at least one note to add money to a cash account.", "error")
                return render_template("add_money.html", form=form,
                                        denominations=Config.CASH_DENOMINATIONS, account_types=account_types)
            amount = denom_amount
        else:
            amount = form.amount.data
            cash_breakdown = None
            if amount is None or amount <= 0:
                flash("Enter a valid amount.", "error")
                return render_template("add_money.html", form=form,
                                        denominations=Config.CASH_DENOMINATIONS, account_types=account_types)

        services.create_transaction(
            user_id=current_user.id, txn_type="income", amount=amount,
            account_id=form.account_id.data, date=form.date.data,
            note=_encrypt_note(form.note.data), cash_breakdown=cash_breakdown,
        )
        flash(f"Added {amount} to your account.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("add_money.html", form=form, denominations=Config.CASH_DENOMINATIONS,
                            account_types=account_types)


# ---------- add / edit / delete expense ----------

@main_bp.route("/add-expense", methods=["GET", "POST"])
@login_required
def add_expense():
    unlock_redirect = _require_unlock()
    if unlock_redirect:
        return unlock_redirect

    form = ExpenseForm()
    _populate_account_choices(form.account_id)
    _populate_category_choices(form.category_id)
    account_types = {a.id: a.account_type for a in _user_accounts()}

    preselect_category = request.args.get("category_id", type=int)
    if request.method == "GET":
        form.date.data = date.today()
        if preselect_category:
            form.category_id.data = preselect_category

    subs = Subcategory.query.filter_by(category_id=form.category_id.data).all() if form.category_id.data else []
    form.subcategory_id.choices = [(0, "— none —")] + [(s.id, s.name) for s in subs]

    if not form.account_id.choices:
        flash("Create an account first before adding an expense.", "error")
        return redirect(url_for("main.account_add"))
    if not form.category_id.choices:
        flash("Create a category first before adding an expense.", "error")
        return redirect(url_for("main.category_add"))

    if form.validate_on_submit():
        account = Account.query.filter_by(id=form.account_id.data, user_id=current_user.id).first()

        cash_breakdown = None
        amount = form.amount.data
        if form.payment_method.data == "Cash":
            given, change = _read_denom_dict("given"), _read_denom_dict("change")
            if given or change:
                if account and account.account_type == "Cash":
                    problems = _validate_cash_given(account.id, given)
                    if problems:
                        flash(_cash_shortage_message(problems), "error")
                        today = date.today()
                        budget = Budget.query.filter_by(user_id=current_user.id, period_key=month_key(today)).first()
                        return render_template("add_expense.html", form=form, budget=budget,
                                                spent_so_far=Decimal("0"), denominations=Config.CASH_DENOMINATIONS,
                                                account_types=account_types)
                cash_amount, cash_breakdown = _expense_cash_from_request()
                if cash_amount <= 0:
                    flash("The change you received can't be more than the cash you gave.", "error")
                    today = date.today()
                    budget = Budget.query.filter_by(user_id=current_user.id, period_key=month_key(today)).first()
                    return render_template("add_expense.html", form=form, budget=budget,
                                            spent_so_far=Decimal("0"), denominations=Config.CASH_DENOMINATIONS,
                                            account_types=account_types)
                amount = cash_amount

        if account and account.balance < amount and account.account_type != "Credit Card":
            flash(f"Heads up: this will take {account.name} negative.", "info")
        sub_id = form.subcategory_id.data or None
        txn = services.create_transaction(
            user_id=current_user.id, txn_type="expense", amount=amount,
            account_id=form.account_id.data, category_id=form.category_id.data,
            subcategory_id=sub_id, payment_method=form.payment_method.data,
            date=form.date.data, note=_encrypt_note(form.note.data), cash_breakdown=cash_breakdown,
        )
        if form.is_recharge.data and form.recharge_duration.data:
            expiry = form.date.data + timedelta(days=form.recharge_duration.data)
            db.session.add(Recharge(
                user_id=current_user.id, transaction_id=txn.id,
                duration_days=form.recharge_duration.data,
                start_date=form.date.data, expiry_date=expiry,
            ))
            db.session.commit()
        flash("Expense recorded.", "success")
        return redirect(url_for("main.dashboard"))

    today = date.today()
    budget = Budget.query.filter_by(user_id=current_user.id, period_key=month_key(today)).first()
    spent_so_far = Decimal("0")
    if budget:
        _, _, spent_so_far = _totals_for_range(today.replace(day=1), today)

    return render_template("add_expense.html", form=form, budget=budget, spent_so_far=spent_so_far,
                            denominations=Config.CASH_DENOMINATIONS, account_types=account_types)


@main_bp.route("/subcategories/<int:category_id>.json")
@login_required
def subcategories_json(category_id):
    category = Category.query.filter_by(id=category_id, user_id=current_user.id).first_or_404()
    return jsonify([{"id": s.id, "name": s.name} for s in category.subcategories])


@main_bp.route("/transactions/<int:txn_id>/edit", methods=["GET", "POST"])
@login_required
def transaction_edit(txn_id):
    unlock_redirect = _require_unlock()
    if unlock_redirect:
        return unlock_redirect

    txn = Transaction.query.filter_by(id=txn_id, user_id=current_user.id).first_or_404()

    if txn.type == "income":
        form = AddMoneyForm(obj=txn)
        _populate_account_choices(form.account_id)
        income_account_types = {a.id: a.account_type for a in _user_accounts()}
        if request.method == "GET":
            form.note.data = _decrypt_note(txn.note)
        if form.validate_on_submit():
            if form.amount.data is None or form.amount.data <= 0:
                flash("Enter a valid amount.", "error")
                return render_template("add_money.html", form=form, denominations=[], editing=True,
                                        account_types=income_account_types)
            # Editing income uses the amount field only (no denomination UI).
            # Clear any previous cash_breakdown so note inventory is reversed
            # cleanly and does not drift out of sync with the new amount.
            services.update_transaction(
                current_user.id, txn.id,
                amount=form.amount.data,
                account_id=form.account_id.data,
                date=form.date.data,
                note=_encrypt_note(form.note.data),
                cash_breakdown=None,
            )
            flash("Updated.", "success")
            return redirect(url_for("main.dashboard"))
        return render_template("add_money.html", form=form, denominations=[], editing=True,
                                account_types=income_account_types)

    form = ExpenseForm(obj=txn)
    _populate_account_choices(form.account_id)
    _populate_category_choices(form.category_id)
    account_types = {a.id: a.account_type for a in _user_accounts()}
    subs = Subcategory.query.filter_by(category_id=txn.category_id).all() if txn.category_id else []
    form.subcategory_id.choices = [(0, "— none —")] + [(s.id, s.name) for s in subs]
    if request.method == "GET":
        form.subcategory_id.data = txn.subcategory_id or 0
        form.note.data = _decrypt_note(txn.note)

    if form.validate_on_submit():
        update_fields = dict(
            account_id=form.account_id.data, category_id=form.category_id.data,
            subcategory_id=form.subcategory_id.data or None,
            payment_method=form.payment_method.data, date=form.date.data,
            note=_encrypt_note(form.note.data),
        )
        if form.payment_method.data == "Cash":
            given, change = _read_denom_dict("given"), _read_denom_dict("change")
            if given or change:
                if form.account_id.data == txn.account_id:
                    # Same account as before: the note's own old "given" amount
                    # is still reflected in current holdings, so add it back
                    # when checking availability for the edited amount.
                    old_breakdown = json.loads(txn.cash_breakdown) if txn.cash_breakdown else {}
                    old_given = old_breakdown.get("given", {})
                else:
                    old_given = {}
                account_obj = Account.query.filter_by(id=form.account_id.data, user_id=current_user.id).first()
                if account_obj and account_obj.account_type == "Cash":
                    problems = _validate_cash_given(account_obj.id, given, old_given=old_given)
                    if problems:
                        flash(_cash_shortage_message(problems), "error")
                        return render_template("add_expense.html", form=form, budget=None,
                                                spent_so_far=Decimal("0"), editing=True,
                                                denominations=Config.CASH_DENOMINATIONS,
                                                account_types=account_types)
                cash_amount, cash_breakdown = _expense_cash_from_request()
                if cash_amount <= 0:
                    flash("The change you received can't be more than the cash you gave.", "error")
                    return render_template("add_expense.html", form=form, budget=None,
                                            spent_so_far=Decimal("0"), editing=True,
                                            denominations=Config.CASH_DENOMINATIONS,
                                            account_types=account_types)
                update_fields["amount"] = cash_amount
                update_fields["cash_breakdown"] = cash_breakdown
            else:
                update_fields["amount"] = form.amount.data
        else:
            update_fields["amount"] = form.amount.data
            update_fields["cash_breakdown"] = None

        services.update_transaction(current_user.id, txn.id, **update_fields)
        flash("Updated.", "success")
        return redirect(url_for("main.dashboard"))

    existing_breakdown = json.loads(txn.cash_breakdown) if txn.cash_breakdown else {}
    return render_template("add_expense.html", form=form, budget=None, spent_so_far=Decimal("0"),
                            editing=True, existing_given=existing_breakdown.get("given", {}),
                            existing_change=existing_breakdown.get("change", {}),
                            denominations=Config.CASH_DENOMINATIONS, account_types=account_types)


@main_bp.route("/transactions/<int:txn_id>/delete", methods=["POST"])
@login_required
def transaction_delete(txn_id):
    try:
        services.delete_transaction(current_user.id, txn_id)
        flash("Transaction deleted.", "info")
    except ValueError:
        abort(404)
    return redirect(request.referrer or url_for("main.dashboard"))


@main_bp.route("/recharges/<int:recharge_id>/dismiss", methods=["POST"])
@login_required
def recharge_dismiss(recharge_id):
    r = Recharge.query.filter_by(id=recharge_id, user_id=current_user.id).first_or_404()
    r.dismissed = True
    db.session.commit()
    flash("Reminder dismissed.", "info")
    return redirect(url_for("main.dashboard"))


# ---------- budget ----------

@main_bp.route("/budget", methods=["GET", "POST"])
@login_required
def budget_view():
    today = date.today()
    pk = month_key(today)
    budget = Budget.query.filter_by(user_id=current_user.id, period_key=pk).first()
    form = BudgetForm(obj=budget)

    if form.validate_on_submit():
        if budget:
            budget.amount = form.amount.data
        else:
            budget = Budget(user_id=current_user.id, period_key=pk, amount=form.amount.data)
            db.session.add(budget)
        db.session.commit()
        flash("Budget saved.", "success")
        return redirect(url_for("main.budget_view"))

    _, _, spent = _totals_for_range(today.replace(day=1), today)
    return render_template("budget.html", form=form, budget=budget, spent=spent, label=month_label(pk))


# ---------- statistics ----------

@main_bp.route("/statistics")
@login_required
def statistics():
    preset = request.args.get("range", "this_month")
    cs = request.args.get("start")
    ce = request.args.get("end")
    custom_start = datetime.strptime(cs, "%Y-%m-%d").date() if cs else None
    custom_end = datetime.strptime(ce, "%Y-%m-%d").date() if ce else None
    start, end = date_range_for(preset, custom_start, custom_end)

    txns, income, expense = _totals_for_range(start, end)
    expenses = [t for t in txns if t.type == "expense"]

    by_category = {}
    for t in expenses:
        name = t.category.name if t.category else "Uncategorized"
        by_category[name] = by_category.get(name, Decimal("0")) + t.amount

    highest = max(expenses, key=lambda t: t.amount, default=None)
    top_category = max(by_category.items(), key=lambda kv: kv[1], default=(None, 0))
    avg = (expense / len(expenses)) if expenses else Decimal("0")

    months = last_n_months(6)
    monthly_totals = []
    for pk in months:
        y, m = map(int, pk.split("-"))
        m_start = date(y, m, 1)
        m_end_day = 28
        while True:
            try:
                date(y, m, m_end_day + 1)
                m_end_day += 1
            except ValueError:
                break
        m_end = date(y, m, m_end_day)
        _, _, m_expense = _totals_for_range(m_start, m_end)
        monthly_totals.append((month_label(pk), float(m_expense)))

    return render_template(
        "statistics.html", start=start, end=end, preset=preset, income=income, expense=expense,
        by_category=by_category, highest=highest, top_category=top_category, avg=avg,
        transaction_count=len(expenses), monthly_totals=monthly_totals,
    )


# ---------- summary ----------

@main_bp.route("/summary")
@login_required
def summary():
    preset = request.args.get("range", "this_month")
    cs = request.args.get("start")
    ce = request.args.get("end")
    custom_start = datetime.strptime(cs, "%Y-%m-%d").date() if cs else None
    custom_end = datetime.strptime(ce, "%Y-%m-%d").date() if ce else None
    start, end = date_range_for(preset, custom_start, custom_end)

    txns, income, expense = _totals_for_range(start, end)
    expenses = [t for t in txns if t.type == "expense"]
    accounts = _user_accounts()
    total_balance = sum((a.balance for a in accounts), Decimal("0"))

    by_category = {}
    for t in expenses:
        name = t.category.name if t.category else "Uncategorized"
        by_category[name] = by_category.get(name, Decimal("0")) + t.amount
    highest = max(expenses, key=lambda t: t.amount, default=None)
    top_category = max(by_category.items(), key=lambda kv: kv[1], default=(None, 0))
    avg = (expense / len(expenses)) if expenses else Decimal("0")

    budget = Budget.query.filter_by(user_id=current_user.id, period_key=month_key(date.today())).first()

    return render_template(
        "summary.html", start=start, end=end, preset=preset, income=income, expense=expense,
        remaining=income - expense, accounts=accounts, total_balance=total_balance,
        highest=highest, top_category=top_category, avg=avg, transaction_count=len(expenses),
        budget=budget,
    )


# ---------- global search ----------

@main_bp.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    results = {"transactions": [], "accounts": [], "categories": []}
    if q:
        like = f"%{q}%"
        q_lower = q.lower()
        # Amount can still be matched in SQL; notes are encrypted, so those
        # have to be decrypted and matched in Python.
        by_amount = Transaction.query.filter(
            Transaction.user_id == current_user.id,
            db.cast(Transaction.amount, db.String).ilike(like)
        ).all()
        all_txns = Transaction.query.filter_by(user_id=current_user.id).all()
        by_note = [t for t in all_txns if q_lower in (_decrypt_note(t.note) or "").lower()]
        seen_ids = set()
        merged = []
        for t in by_amount + by_note:
            if t.id not in seen_ids:
                seen_ids.add(t.id)
                merged.append(t)
        merged.sort(key=lambda t: t.date, reverse=True)
        results["transactions"] = merged[:50]

        results["accounts"] = Account.query.filter(
            Account.user_id == current_user.id, Account.name.ilike(like)
        ).all()
        results["categories"] = Category.query.filter(
            Category.user_id == current_user.id, Category.name.ilike(like)
        ).all()
    return render_template("search.html", q=q, results=results)


# ---------- backup / restore ----------

@main_bp.route("/backup")
@login_required
def backup_page():
    return render_template("backup.html", restore_form=RestoreForm())


@main_bp.route("/backup/export", methods=["POST"])
@login_required
def backup_export():
    password = request.form.get("password", "")
    if len(password) < 8:
        flash("Choose a backup password with at least 8 characters.", "error")
        return redirect(url_for("main.backup_page"))
    blob = export_user_data(current_user, password)
    return send_file(
        io.BytesIO(blob), as_attachment=True,
        download_name=f"moneymanager-backup-{date.today().isoformat()}.mmb",
        mimetype="application/octet-stream",
    )


@main_bp.route("/backup/restore", methods=["POST"])
@login_required
def backup_restore():
    form = RestoreForm()
    file = request.files.get("backup_file")
    if not file or file.filename == "":
        flash("Choose a backup file to restore.", "error")
        return redirect(url_for("main.backup_page"))
    if not form.validate_on_submit():
        flash("Enter the backup password.", "error")
        return redirect(url_for("main.backup_page"))
    try:
        import_user_data(current_user, form.password.data, file.read())
        flash("Backup restored successfully.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("main.dashboard"))


# ---------- push notification subscription ----------

@main_bp.route("/notifications/subscribe", methods=["POST"])
@login_required
def notifications_subscribe():
    data = request.get_json(force=True, silent=True) or {}
    endpoint = data.get("endpoint")
    keys = data.get("keys", {})
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return jsonify({"ok": False, "error": "invalid subscription"}), 400

    existing = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if existing is None:
        db.session.add(PushSubscription(
            user_id=current_user.id, endpoint=endpoint,
            p256dh=keys["p256dh"], auth=keys["auth"],
        ))
        db.session.commit()
    return jsonify({"ok": True})


@main_bp.route("/notifications/unsubscribe", methods=["POST"])
@login_required
def notifications_unsubscribe():
    data = request.get_json(force=True, silent=True) or {}
    endpoint = data.get("endpoint")
    PushSubscription.query.filter_by(user_id=current_user.id, endpoint=endpoint).delete()
    db.session.commit()
    return jsonify({"ok": True})


@main_bp.route("/settings")
@login_required
def settings():
    vapid_public = Config.VAPID_PUBLIC_KEY
    return render_template("settings.html", vapid_public=vapid_public, note_unlocked=(_enc_key() is not None))
