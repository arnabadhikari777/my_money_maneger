"""
Endpoints meant to be called by an external scheduler (PythonAnywhere's
'Tasks' tab), not by the browser. Protected by a shared secret so random
visitors can't trigger pushes to every user.
"""
import os
import json
from datetime import date
from flask import Blueprint, request, jsonify, current_app

from app.extensions import db
from app.models import PushSubscription, Recharge

api_bp = Blueprint("api", __name__, url_prefix="/api")

CRON_SECRET = os.environ.get("CRON_SECRET", "")


def _recharge_label(recharge):
    txn = recharge.transaction
    if txn is None:
        return "Recharge"
    if txn.category and txn.subcategory:
        return f"{txn.category.name} · {txn.subcategory.name}"
    if txn.category:
        return txn.category.name
    return "Recharge"


@api_bp.route("/pending-reminders")
def pending_reminders():
    """Called once a day by an external runner (e.g. GitHub Actions), which
    does NOT have PythonAnywhere's free-plan outbound-network restriction and
    can actually reach Google/Mozilla's push services. This endpoint itself
    makes no outbound calls - it only reads the local database and returns
    which push messages need sending, then marks them as sent so the same
    reminder is never repeated.

    curl "https://yourusername.pythonanywhere.com/api/pending-reminders?secret=YOUR_SECRET"
    """
    secret = request.args.get("secret", "")
    if not CRON_SECRET or secret != CRON_SECRET:
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    today = date.today()
    to_send = []

    recharges = Recharge.query.filter_by(dismissed=False).filter(Recharge.expiry_date >= today).all()
    for r in recharges:
        days_left = (r.expiry_date - today).days
        fire = None
        if days_left == 7 and not r.notified_7d:
            fire = 7
        elif days_left == 3 and not r.notified_3d:
            fire = 3
        if fire is None:
            continue

        subs = PushSubscription.query.filter_by(user_id=r.user_id).all()
        if not subs:
            continue

        label = _recharge_label(r)
        body = f"{label}: {days_left} din(s) left ({r.expiry_date.strftime('%d %b')})"
        for sub in subs:
            to_send.append({
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                "title": "Money Manager Reminder",
                "body": body,
                "url": "/",
            })

        if fire == 7:
            r.notified_7d = True
        else:
            r.notified_3d = True

    db.session.commit()
    return jsonify(to_send)


@api_bp.route("/subscription-cleanup", methods=["POST"])
def subscription_cleanup():
    """Called by the external runner when a push send comes back 404/410
    (subscription expired or the user uninstalled/revoked it), so stale
    subscriptions don't pile up in the database."""
    secret = request.args.get("secret", "")
    if not CRON_SECRET or secret != CRON_SECRET:
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    data = request.get_json(force=True, silent=True) or {}
    endpoint = data.get("endpoint")
    if not endpoint:
        return jsonify({"ok": False, "error": "endpoint required"}), 400

    PushSubscription.query.filter_by(endpoint=endpoint).delete()
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/send-budget-reminders", methods=["POST", "GET"])
def send_budget_reminders():
    """DEPRECATED on PythonAnywhere's free plan: this tries to call Google/
    Mozilla's push services directly from PythonAnywhere, which the free
    plan's outbound-network whitelist blocks. Kept only for reference / for
    accounts on a paid plan with full internet access. Use
    /api/pending-reminders + an external runner (see /api/pending-reminders)
    instead on the free plan.
    """
    secret = request.args.get("secret", "")
    if not CRON_SECRET or secret != CRON_SECRET:
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    vapid_public = current_app.config.get("VAPID_PUBLIC_KEY")
    vapid_private = current_app.config.get("VAPID_PRIVATE_KEY")
    if not vapid_public or not vapid_private:
        return jsonify({"ok": False, "error": "VAPID keys not configured"}), 500

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return jsonify({"ok": False, "error": "pywebpush not installed"}), 500

    sent, failed = 0, 0
    subs = PushSubscription.query.all()
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=json.dumps({
                    "title": "Money Manager",
                    "body": "Time for a quick check of your spending and budget.",
                    "url": "/",
                }),
                vapid_private_key=vapid_private,
                vapid_claims={"sub": current_app.config.get("VAPID_CLAIM_EMAIL")},
            )
            sent += 1
        except WebPushException as e:
            failed += 1
            # Expired/invalid subscriptions get cleaned up automatically.
            if e.response is not None and e.response.status_code in (404, 410):
                db.session.delete(sub)
    db.session.commit()
    return jsonify({"ok": True, "sent": sent, "failed": failed})
