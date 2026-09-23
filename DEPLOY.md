# Deploying to PythonAnywhere

## 1. Upload the code
- On PythonAnywhere, open a **Bash console**.
- Upload this project (via the Files tab, or `git clone` if you push it to a repo) to
  `/home/YOURUSERNAME/moneymanager`.

## 2. Create a virtualenv and install dependencies
```bash
cd ~/moneymanager
python3.10 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 3. Set environment variables
Easiest approach: create `~/moneymanager/.env` style values directly in the WSGI file (step 5),
or set them under **Web tab → Environment variables** if your plan supports it. At minimum set:
- `SECRET_KEY` — long random string
- `CRON_SECRET` — long random string (protects the reminder endpoint)
- `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` — only if you want push reminders (see step 7)

## 4. Create the Web App
- **Web tab → Add a new web app → Manual configuration → Python 3.10**.
- Set the virtualenv path to `/home/YOURUSERNAME/moneymanager/venv`.

## 5. Edit the WSGI configuration file
PythonAnywhere gives you a WSGI file path like
`/var/www/YOURUSERNAME_pythonanywhere_com_wsgi.py`. Replace its contents with:

```python
import sys, os

path = '/home/YOURUSERNAME/moneymanager'
if path not in sys.path:
    sys.path.append(path)

os.environ['SECRET_KEY'] = 'paste-your-long-random-secret-here'
os.environ['CRON_SECRET'] = 'paste-another-long-random-secret-here'
os.environ['DATABASE_URL'] = 'sqlite:////home/YOURUSERNAME/moneymanager/instance/moneymanager.db'
os.environ['VAPID_PUBLIC_KEY'] = ''   # optional
os.environ['VAPID_PRIVATE_KEY'] = ''  # optional

from wsgi import app as application
```

Note the **absolute** sqlite path (4 slashes) — PythonAnywhere's working directory for the WSGI
process isn't your project folder.

## 6. Static files
Under **Web tab → Static files**, map:
- URL `/static/` → Directory `/home/YOURUSERNAME/moneymanager/app/static/`

## 7. (Optional) Enable push notification reminders
1. Generate a VAPID key pair: `pip install py-vapid && vapid --gen` (do this once, keep the private key secret).
2. Put the public/private keys into the WSGI file's environment variables above.
3. Under **Tasks tab**, add a scheduled task (every day is the coarsest built-in interval on
   the free tier — for true 4–5 hour spacing you'll need a paid plan's finer scheduling, or
   3–6 separate daily tasks at different times) running:
   ```bash
   curl -X POST "https://YOURUSERNAME.pythonanywhere.com/api/send-budget-reminders?secret=YOUR_CRON_SECRET"
   ```
4. In the app, go to **Settings → Enable Reminders** on your phone to subscribe that device.

## 8. Reload
Hit the green **Reload** button on the Web tab. Visit `https://YOURUSERNAME.pythonanywhere.com`.

## 9. Install on Android
Open the site in Chrome on your phone → menu → **Add to Home screen** (or use the
**Install App** button on the Settings page once Chrome detects it's installable).
PythonAnywhere serves everything over HTTPS by default, which is required for PWA install
and push notifications.

## Notes on security
- **Transaction notes are encrypted per-user.** Each note is encrypted with a key derived
  from that user's real password (PBKDF2 + Fernet/AES). The key only ever lives in the
  signed session cookie for an active login — it is never written to the database. This
  means opening the `.db` file directly (even as the server admin) will not reveal note
  text; you'd need that specific user's password. A "Remember me" auto-login can't rederive
  this key (it doesn't carry the password), so the app will prompt to "Unlock" with the
  password again before showing or creating notes in that case.
- Amounts, balances, categories and account names are **not** encrypted — they're needed
  for SQL-level totals (budgets, statistics, dashboards). Only free-text notes are protected.
- Passwords are hashed with Werkzeug's `generate_password_hash` (PBKDF2), never stored in plain text.
- CSRF protection is enabled on every form via Flask-WTF.
- Backups are encrypted client-independent, using a key derived from a password you choose at
  export/restore time (PBKDF2-HMAC-SHA256 + Fernet/AES from the `cryptography` library) — no
  custom crypto, and the password itself is never written to disk.
- Card/account numbers: only the last 4 digits are ever stored; CVV/PIN/OTP/passwords are never
  collected by this app at all.
- Every account/category/transaction query is scoped to `current_user.id`, so one user's data is
  never reachable by another user's session.
