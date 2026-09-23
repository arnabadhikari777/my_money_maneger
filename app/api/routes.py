"""
Endpoints meant to be called by an external scheduler (PythonAnywhere's
'Tasks' tab), not by the browser. Protected by a shared secret so random
visitors can't trigger pushes to every user.
"""
import os
import json
from flask import Blueprint, request, jsonify, current_app

from app.extensions import db
from app.models import PushSubscription

api_bp = Blueprint("api", __name__, url_prefix="/api")

CRON_SECRET = os.environ.get("CRON_SECRET", "")


@api_bp.route("/send-budget-reminders", methods=["POST", "GET"])
def send_budget_reminders():
    """Call this every 4-5 hours via a PythonAnywhere scheduled Task, e.g.:
    curl -X POST https://yourusername.pythonanywhere.com/api/send-budget-reminders?secret=YOUR_SECRET
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
