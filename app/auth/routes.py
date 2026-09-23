import secrets
from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import db
from app.models import User, Category, Subcategory
from app.auth.forms import RegisterForm, LoginForm, UnlockForm
from app.crypto import derive_key
from config import Config

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _seed_default_categories(user):
    for cat_name, subs in Config.DEFAULT_CATEGORIES.items():
        cat = Category(user_id=user.id, name=cat_name)
        db.session.add(cat)
        db.session.flush()
        for sub_name in subs:
            db.session.add(Subcategory(category_id=cat.id, name=sub_name))
    db.session.commit()


def _unlock_session(user, plaintext_password):
    """Derive this user's note-encryption key from their real password and
    stash it in the session (never written to the DB)."""
    if not user.enc_salt:
        user.enc_salt = secrets.token_hex(16)
        db.session.commit()
    key = derive_key(plaintext_password, user.enc_salt)
    session["enc_key"] = key.decode("utf-8")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = RegisterForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data.strip()).first():
            flash("That username is already taken.", "error")
            return render_template("auth/register.html", form=form)
        user = User(username=form.username.data.strip(), email=form.email.data.strip() or None)
        user.set_password(form.password.data)
        user.enc_salt = secrets.token_hex(16)
        db.session.add(user)
        db.session.commit()
        _seed_default_categories(user)
        _unlock_session(user, form.password.data)
        login_user(user)
        flash("Welcome! Your account has been created.", "success")
        return redirect(url_for("main.dashboard"))
    return render_template("auth/register.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data.strip()).first()
        if user is None or not user.check_password(form.password.data):
            flash("Invalid username or password.", "error")
            return render_template("auth/login.html", form=form)
        _unlock_session(user, form.password.data)
        login_user(user, remember=form.remember.data)
        next_page = request.args.get("next")
        return redirect(next_page or url_for("main.dashboard"))
    return render_template("auth/login.html", form=form)


@auth_bp.route("/unlock", methods=["GET", "POST"])
@login_required
def unlock():
    """Re-enter your password to restore access to encrypted notes -
    needed after a 'Remember me' auto-login, which doesn't carry the password."""
    form = UnlockForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.password.data):
            flash("Wrong password.", "error")
            return render_template("auth/unlock.html", form=form)
        _unlock_session(current_user, form.password.data)
        flash("Unlocked.", "success")
        next_page = request.args.get("next")
        return redirect(next_page or url_for("main.dashboard"))
    return render_template("auth/unlock.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    session.pop("enc_key", None)
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
