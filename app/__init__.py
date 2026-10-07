import os
from flask import Flask, send_from_directory
from config import Config
from app.extensions import db, login_manager, csrf


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.auth.routes import auth_bp
    from app.main.routes import main_bp
    from app.api.routes import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    # /api/* is called by an external script (GitHub Actions) with a shared
    # secret, not by a browser form - it has no CSRF token to send.
    csrf.exempt(api_bp)

    # The service worker MUST be served from the site root. A worker loaded
    # from /static/sw.js can only control URLs under /static/, so it never
    # controlled the actual app pages: navigator.serviceWorker.ready never
    # resolved on the Settings page, which silently broke "Enable reminders"
    # (no push subscription could ever be created) and offline caching.
    @app.route("/sw.js")
    def service_worker():
        resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # Security headers (baseline; add HSTS once confirmed permanently on HTTPS)
    @app.after_request
    def set_secure_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return resp

    from app import filters  # noqa: F401  (registers Jinja filters)
    filters.register(app)

    with app.app_context():
        db.create_all()
        # Existing databases made before the "purpose" column existed: add it
        # in place (keeps all old data). Safe to run on every start.
        try:
            from sqlalchemy import inspect, text
            cols = [c["name"] for c in inspect(db.engine).get_columns("accounts")]
            if "purpose" not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE accounts ADD COLUMN purpose VARCHAR(100)"))
        except Exception as exc:  # never block app start-up
            app.logger.warning("Could not add accounts.purpose column: %s", exc)

    return app
