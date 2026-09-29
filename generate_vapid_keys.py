"""
Run this ONCE on PythonAnywhere (in the Bash console, inside your activated
venv) to generate a fresh, CORRECTLY-FORMATTED VAPID key pair for push
notifications:

    cd ~/moneymanager
    source venv/bin/activate
    python3 generate_vapid_keys.py

Why a new script: the key-generation one-liner used earlier produced a
PEM-formatted private key ("-----BEGIN PRIVATE KEY-----..."), but the
`pywebpush` library that GitHub Actions uses to actually send the push
expects the RAW private key value instead, base64url-encoded with no
padding. Passing it a PEM string makes it raise a decode error on every
single send - which is the actual reason notifications haven't been
arriving. This script outputs the correct raw format directly.

If you already generated keys before and put subscriptions in the database,
regenerating will invalidate those - anyone who tapped "Enable reminders"
before will need to tap it again after you update the PUBLIC key on the
server (the private key change alone doesn't affect them, but if you also
change the public key, their old subscription is tied to the old key pair).
"""
import base64
from py_vapid import Vapid01
from cryptography.hazmat.primitives import serialization

v = Vapid01()
v.generate_keys()

# Public key: uncompressed EC point, base64url, no padding.
# This is what the frontend uses to subscribe the browser to push.
pub_bytes = v.public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint,
)
public_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()

# Private key: raw 32-byte scalar, base64url, no padding.
# This is the format pywebpush actually expects - NOT a PEM string.
priv_value = v.private_key.private_numbers().private_value
priv_bytes = priv_value.to_bytes(32, "big")
private_b64 = base64.urlsafe_b64encode(priv_bytes).rstrip(b"=").decode()

print()
print("=" * 60)
print("VAPID_PUBLIC_KEY  (put this in PythonAnywhere's WSGI file)")
print("=" * 60)
print(public_b64)
print()
print("=" * 60)
print("VAPID_PRIVATE_KEY (put this in BOTH the WSGI file AND the")
print("                   GitHub secret of the same name)")
print("=" * 60)
print(private_b64)
print()
