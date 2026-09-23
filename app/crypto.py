"""
Per-user field-level encryption for sensitive free-text data (transaction notes).

Design:
- Each user has a random `enc_salt` (stored in the DB - not secret on its own).
- The actual encryption key is derived from the user's PLAINTEXT password + their
  salt, using PBKDF2. This key only ever exists in memory, inside the signed
  session cookie, for the duration of a login session that had the real password
  typed into it - it is NEVER written to the database.
- This means: someone with only database access (e.g. opening the .db file
  directly) cannot decrypt notes, even the app's own administrator, unless they
  also know that specific user's login password.

Trade-off: a "Remember me" auto-login (via Flask-Login's remember cookie) does
NOT carry the password, so it can't rederive the key. In that case the app asks
for the password again ("Unlock") before showing/creating notes.
"""
import base64
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Lower than the backup export's iteration count on purpose: this one runs on
# every login/unlock (a user-facing action), where backup export is rare.
PBKDF2_ITERATIONS = 200_000


def derive_key(password: str, salt_hex: str) -> bytes:
    salt = bytes.fromhex(salt_hex)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def encrypt_text(key: bytes, plaintext):
    if not plaintext:
        return None
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_text(key: bytes, token):
    """Returns the plaintext, or None if there's no token, no/wrong key, or
    the data predates encryption being enabled (stored as plain text)."""
    if not token:
        return None
    try:
        return Fernet(key).decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None
