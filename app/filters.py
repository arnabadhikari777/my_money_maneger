from decimal import Decimal
from flask import session as flask_session
from app.crypto import decrypt_text


def register(app):
    @app.template_filter("inr")
    def inr(value):
        try:
            value = Decimal(value)
        except Exception:
            return value
        s = f"{abs(value):,.2f}"
        sign = "-" if value < 0 else ""
        return f"{sign}\u20b9{s}"

    @app.template_filter("pct")
    def pct(numerator, denominator):
        try:
            numerator = float(numerator)
            denominator = float(denominator)
            if denominator == 0:
                return 0
            return round((numerator / denominator) * 100, 1)
        except Exception:
            return 0

    @app.template_filter("decrypt_note")
    def decrypt_note(value):
        """Decrypts a transaction note using this session's per-user key.
        Shows a lock placeholder if the key isn't available (e.g. after a
        'Remember me' auto-login) or if it doesn't match (wrong data/legacy)."""
        if not value:
            return ""
        key_str = flask_session.get("enc_key")
        if not key_str:
            return "🔒 Unlock to view"
        plain = decrypt_text(key_str.encode("utf-8"), value)
        return plain if plain is not None else "🔒 (couldn't decrypt)"
