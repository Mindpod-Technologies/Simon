"""Owner authentication for the Simon web UI.

Design:
- The owner sets a password during first-run onboarding; only its PBKDF2
  hash is stored (``SIMON_OWNER_PASSWORD_HASH`` in .env).
- Sessions are stateless signed cookies: ``<expiry>.<hmac>``, HMAC keyed on
  the password hash itself — changing the password invalidates every
  session, and sessions survive service restarts.
- No password set = first-run: only /setup, /api/setup/*, /api/login and
  static assets are reachable; everything else funnels to the wizard.
- Login attempts are rate-limited (5 failures → 60 s lockout, in-memory).

Chat channels (Slack/Telegram/Teams) are unaffected — they authenticate
through their own platforms and allowlists, not this HTTP layer.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

_ITERATIONS = 200_000
_SESSION_TTL = 7 * 24 * 3600  # 7 days
_LOCKOUT_AFTER = 5
_LOCKOUT_SECONDS = 60

COOKIE_NAME = "simon_auth"

# Paths reachable without a session (login/setup status/static only).
# NOTE: /api/setup/save and /api/setup/pull_* are NOT here — they are
# first-run-only: web.py's gate allows them only while no password exists.
_PUBLIC_PREFIXES = ("/static/",)
_PUBLIC_PATHS = {
    "/login", "/setup", "/api/login", "/api/setup/status",
    "/favicon.ico",
}

# Extra paths allowed only during first-run (no password set yet).
_FIRST_RUN_PATHS = {
    "/api/setup/save", "/api/setup/pull_model", "/api/setup/pull_status",
}

_failures = 0
_locked_until = 0.0


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256, stored as ``pbkdf2$<iterations>$<salt>$<hash>``."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against the stored hash."""
    try:
        _algo, iterations, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt),
            int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, AttributeError):
        return False


def make_session(stored_hash: str) -> str:
    """Issue a signed session token valid for ``_SESSION_TTL`` seconds."""
    expiry = str(int(time.time()) + _SESSION_TTL)
    sig = hmac.new(stored_hash.encode(), expiry.encode(),
                   hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"


def check_session(token: str, stored_hash: str) -> bool:
    """Validate a session token against the current password hash."""
    if not token or not stored_hash:
        return False
    try:
        expiry, sig = token.split(".", 1)
        expected = hmac.new(stored_hash.encode(), expiry.encode(),
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected) and \
            int(expiry) > int(time.time())
    except (ValueError, AttributeError):
        return False


def login_throttled() -> bool:
    """True when too many recent failures have triggered a lockout."""
    return time.time() < _locked_until


def record_login(success: bool) -> None:
    """Track failures; lock out after ``_LOCKOUT_AFTER`` consecutive misses."""
    global _failures, _locked_until
    if success:
        _failures = 0
        return
    _failures += 1
    if _failures >= _LOCKOUT_AFTER:
        _failures = 0
        _locked_until = time.time() + _LOCKOUT_SECONDS


def is_public_path(path: str, first_run: bool = False) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    if first_run and path in _FIRST_RUN_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


def password_hash(settings) -> str:
    return getattr(settings, "simon_owner_password_hash", "") or ""
