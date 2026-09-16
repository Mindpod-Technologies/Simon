"""Tests for simon.licensing.

Two layers:
- Most tests use an EPHEMERAL Ed25519 keypair generated in-fixture, with
  ``licensing._VENDOR_PUBLIC_KEY_HEX`` monkeypatched to match — so the suite
  needs no secret material and runs anywhere.
- ``test_shipped_keypair_matches_vendor_keygen`` mints a key with the real
  vendor keygen (kept OUTSIDE this repo) and verifies it against the public
  key embedded in ``simon/licensing.py``. Skipped wherever the vendor keygen
  is unavailable (CI, customer machines).
"""

from __future__ import annotations

import base64
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import simon.licensing as licensing
from simon.licensing import LicenseStatus, check_license, require_license_or_exit

VENDOR_KEYGEN = Path.home() / "simon-vendor" / "keygen.py"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_key(plan: str, email: str, exp: str, private_key) -> str:
    payload = json.dumps({"email": email, "exp": exp},
                         separators=(",", ":")).encode()
    payload_b64 = _b64url_encode(payload)
    sig_b64 = _b64url_encode(private_key.sign(f"{plan}.{payload_b64}".encode("ascii")))
    return f"SIMON-{plan}-{payload_b64}.{sig_b64}"


@pytest.fixture()
def vendor_keypair(monkeypatch):
    """Ephemeral keypair; patches licensing to trust it. Returns make_key()."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, PublicFormat,
    )
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    monkeypatch.setattr(licensing, "_VENDOR_PUBLIC_KEY_HEX", pub_hex)

    def make_key(plan: str, email: str, exp: str) -> str:
        return _make_key(plan, email, exp, priv)

    return make_key


def make_settings(key: str = "", require: bool = False) -> SimpleNamespace:
    """Settings stub with just the licensing fields."""
    return SimpleNamespace(simon_license_key=key, simon_require_license=require)


def future_date() -> str:
    """An ISO date comfortably in the future."""
    return (date.today() + timedelta(days=365)).isoformat()


def past_date() -> str:
    """An ISO date comfortably in the past."""
    return (date.today() - timedelta(days=30)).isoformat()


def test_keygen_pro_key_validates(vendor_keypair) -> None:
    """A freshly minted pro key validates as pro with email + expiry."""
    key = vendor_keypair("pro", "customer@x.com", future_date())
    assert key.startswith("SIMON-pro-")
    status = check_license(make_settings(key, require=True))
    assert status.valid
    assert status.plan == "pro"
    assert status.email == "customer@x.com"
    assert status.expires == future_date()


def test_keygen_business_key_validates_without_expiry(vendor_keypair) -> None:
    """A business key with no expiry is valid and reports expires=''."""
    key = vendor_keypair("business", "acme@corp.com", "")
    status = check_license(make_settings(key, require=True))
    assert status.valid
    assert status.plan == "business"
    assert status.email == "acme@corp.com"
    assert status.expires == ""


def test_tampered_signature_is_invalid(vendor_keypair) -> None:
    """Flipping signature bytes fails verification."""
    key = vendor_keypair("pro", "customer@x.com", future_date())
    payload_part, sig_b64 = key.split(".", 1)
    raw = bytearray(base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4)))
    raw[0] ^= 0xFF
    tampered_sig = base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode()
    status = check_license(make_settings(f"{payload_part}.{tampered_sig}", require=True))
    assert not status.valid
    assert "signature" in status.reason.lower()


def test_tampered_payload_is_invalid(vendor_keypair) -> None:
    """Re-encoding a different email under the original signature fails."""
    key = vendor_keypair("pro", "customer@x.com", future_date())
    _, sig_b64 = key.split(".", 1)
    forged = base64.urlsafe_b64encode(
        json.dumps({"email": "pirate@evil.com", "exp": future_date()}).encode()
    ).rstrip(b"=").decode()
    status = check_license(make_settings(f"SIMON-pro-{forged}.{sig_b64}", require=True))
    assert not status.valid


def test_expired_key_is_invalid_with_reason(vendor_keypair) -> None:
    """A well-signed but expired key is rejected and says why."""
    key = vendor_keypair("pro", "customer@x.com", past_date())
    status = check_license(make_settings(key, require=True))
    assert not status.valid
    assert status.plan == "pro"
    assert "expired" in status.reason.lower()


def test_empty_key_not_required_is_trial() -> None:
    """Default posture: no key, not required → valid trial (OSS unaffected)."""
    status = check_license(make_settings("", require=False))
    assert status.valid
    assert status.plan == "trial"


def test_empty_key_required_is_invalid() -> None:
    """SIMON_REQUIRE_LICENSE=true with an empty key refuses to start."""
    status = check_license(make_settings("", require=True))
    assert not status.valid
    assert status.reason


def test_garbage_key_is_invalid_and_never_raises() -> None:
    """Arbitrary junk yields an invalid status, not an exception."""
    for junk in ("hello", "SIMON", "SIMON-pro-...", "SIMON-gold-xxx.yyy", "..."):
        status = check_license(make_settings(junk, require=True))
        assert not status.valid, junk
        assert status.reason


def test_require_license_or_exit_exits_on_invalid(capsys: pytest.CaptureFixture) -> None:
    """The startup gate prints a polite message and exits(1) when invalid."""
    exits: list[int] = []
    status = require_license_or_exit(make_settings("", require=True),
                                     exit_func=exits.append)
    assert exits == [1]
    assert not status.valid
    assert "license" in capsys.readouterr().err.lower()


def test_require_license_or_exit_passes_on_valid(vendor_keypair) -> None:
    """The startup gate returns the status without exiting when valid."""
    key = vendor_keypair("pro", "customer@x.com", future_date())
    exits: list[int] = []
    status = require_license_or_exit(make_settings(key, require=True),
                                     exit_func=exits.append)
    assert exits == []
    assert isinstance(status, LicenseStatus)
    assert status.plan == "pro"


def test_real_settings_object_roundtrip(vendor_keypair) -> None:
    """check_license works against the real pydantic Settings class."""
    from simon.config import Settings
    key = vendor_keypair("business", "acme@corp.com", future_date())
    settings = Settings(simon_license_key=key, simon_require_license=True)
    status = check_license(settings)
    assert status.valid and status.plan == "business"


@pytest.mark.skipif(not VENDOR_KEYGEN.exists(),
                    reason="vendor keygen lives outside the repo")
def test_shipped_keypair_matches_vendor_keygen() -> None:
    """The PUBLIC key embedded in licensing.py must match the vendor's real
    PRIVATE key — otherwise every key we sell is rejected. Runs only on the
    vendor machine (no monkeypatching: this checks the real shipped value)."""
    sys.path.insert(0, str(VENDOR_KEYGEN.parent))
    import keygen  # vendor tool, outside the repo
    key = keygen.make_key("pro", "verify@mindpodtech.com", future_date())
    status = check_license(make_settings(key, require=True))
    assert status.valid, f"shipped public key does not match vendor keygen: {status.reason}"
    assert status.plan == "pro"
