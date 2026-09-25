"""
Runs on GitHub Actions (not on PythonAnywhere) once a day.

Why this lives here and not on PythonAnywhere: sending a real push
notification means talking to Google/Mozilla's push servers
(fcm.googleapis.com etc), and PythonAnywhere's free plan blocks outbound
requests to anything not on its whitelist - which does not include those
push services. GitHub Actions has no such restriction, so it does the actual
sending; PythonAnywhere only ever answers "here's what needs sending" over
a normal HTTPS GET request (which free-plan PythonAnywhere can serve fine,
since that's an inbound request to PythonAnywhere, not an outbound one).

Required environment variables (set as GitHub Secrets):
  PYTHONANYWHERE_API_URL   e.g. https://yourusername.pythonanywhere.com/api/pending-reminders
  CRON_SECRET              must match the CRON_SECRET set in your WSGI file
  VAPID_PRIVATE_KEY        must match the VAPID_PRIVATE_KEY set in your WSGI file
  VAPID_CLAIM_EMAIL        optional, e.g. mailto:you@example.com
"""
import os
import sys
import json
import requests
from pywebpush import webpush, WebPushException

API_URL = os.environ["PYTHONANYWHERE_API_URL"]
CRON_SECRET = os.environ["CRON_SECRET"]
VAPID_PRIVATE_KEY = os.environ["VAPID_PRIVATE_KEY"]
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL") or "mailto:example@example.com"

CLEANUP_URL = API_URL.rsplit("/", 1)[0] + "/subscription-cleanup"


def main():
    resp = requests.get(API_URL, params={"secret": CRON_SECRET}, timeout=30)
    resp.raise_for_status()
    reminders = resp.json()

    if not reminders:
        print("No reminders due today.")
        return

    sent, failed = 0, 0
    for r in reminders:
        try:
            webpush(
                subscription_info={"endpoint": r["endpoint"], "keys": r["keys"]},
                data=json.dumps({"title": r["title"], "body": r["body"], "url": r.get("url", "/")}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIM_EMAIL},
            )
            sent += 1
            print(f"Sent: {r['body']}")
        except WebPushException as e:
            failed += 1
            print(f"Failed ({e}): {r['body']}", file=sys.stderr)
            status = e.response.status_code if e.response is not None else None
            if status in (404, 410):
                # Subscription is gone (browser data cleared, uninstalled, etc.)
                # - tell PythonAnywhere to forget it so it stops being tried.
                try:
                    requests.post(
                        CLEANUP_URL,
                        params={"secret": CRON_SECRET},
                        json={"endpoint": r["endpoint"]},
                        timeout=15,
                    )
                except requests.RequestException:
                    pass

    print(f"Done. Sent {sent}, failed {failed}.")


if __name__ == "__main__":
    main()
