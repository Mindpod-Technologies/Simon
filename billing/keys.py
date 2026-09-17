"""License key minting for billing fulfillment.

Signs SIMON-<plan>-<payload>.<sig> keys using the vendor Ed25519 private
key supplied via the SIMON_LICENSE_PRIVATE_KEY environment variable. The
key never lives in this repository — production value sits in the server's
.env (gitignored); tests generate their own throwaway keypair.
"""

from __future__ import annotations

import base64
import json
import os


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_key(plan: str, email: str, exp: str,
             private_key_hex: str = "") -> str:
    """Create a signed license key. Raises ValueError/RuntimeError on
    bad input or a missing private key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    from simon.licensing import VALID_PLANS

    if plan not in VALID_PLANS:
        raise ValueError(f"plan must be one of {VALID_PLANS}")
    if "@" not in (email or ""):
        raise ValueError(f"customer email looks wrong: {email!r}")
    private_key_hex = (private_key_hex
                       or os.environ.get("SIMON_LICENSE_PRIVATE_KEY", ""))
    if not private_key_hex:
        raise RuntimeError(
            "SIMON_LICENSE_PRIVATE_KEY is not set — cannot mint keys")
    private_key = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(private_key_hex))
    payload = {"email": email, "exp": exp}
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signed = f"{plan}.{payload_b64}".encode("ascii")
    sig_b64 = _b64url_encode(private_key.sign(signed))
    return f"SIMON-{plan}-{payload_b64}.{sig_b64}"


def expiry_for(plan: str, today=None) -> str:
    """License expiry policy: pro = 1 year; business = perpetual."""
    if plan == "pro":
        from datetime import date, timedelta
        day = today or date.today()
        return (day + timedelta(days=365)).isoformat()
    return ""  # business: no expiry
