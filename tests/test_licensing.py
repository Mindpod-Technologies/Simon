"""Tests for simon.licensing using the REAL shipped keypair.

Keys are minted with the vendor private key in ``tools/keygen.py`` and
verified against the public key embedded in ``simon/licensing.py`` — no
network, no mocking of the crypto itself.
"""

from __future__ import annotations

import base64
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import keygen  # noqa: E402
from simon.licensing import LicenseStatus, check_license, require_license_or_exit  # noqa: E402


def make_settings(key: str = "", require: bool = False) -> SimpleNamespace:
    """Settings stub with just the licensing fields."""
    return SimpleNamespace(simon_license_key=key, simon_require_license=require)


def future_date() -> str:
    """An ISO date comfortably in the future."""
    return (date.today() + timedelta(days=365)).isoformat()


def past_date() -> str:
    """An ISO date comfortably in the past."""
    return (date.today() - timedelta(days=30)).isoformat()


def test_keygen_pro_key_validates() -> None:
    """A freshly minted pro key validates as pro with email + expiry."""
    key = keygen.make_key("pro", "customer@x.com", future_date())
    assert key.startswith("SIMON-pro-")
    status = check_license(make_settings(key, require=True))
    assert status.valid
    assert status.plan == "pro"
    assert status.email == "customer@x.com"
    assert status.expires == future_date()


def test_keygen_business_key_validates_without_expiry() -> None:
    """A business key with no expiry is valid and reports expires=''."""
    key = keygen.make_key("business", "acme@corp.com", "")
    status = check_license(make_settings(key, require=True))
    assert status.valid
    assert status.plan == "business"
    assert status.email == "acme@corp.com"
    assert status.expires == ""


def test_tampered_signature_is_invalid() -> None:
    """Flipping signature bytes fails verification."""
    key = keygen.make_key("pro", "customer@x.com", future_date())
    payload_part, sig_b64 = key.split(".", 1)
    raw = bytearray(base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4)))
    raw[0] ^= 0xFF
    tampered_sig = base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode()
    status = check_license(make_settings(f"{payload_part}.{tampered_sig}", require=True))
    assert not status.valid
    assert "signature" in status.reason.lower()


def test_tampered_payload_is_invalid() -> None:
    """Re-encoding a different email under the original signature fails."""
    key = keygen.make_key("pro", "customer@x.com", future_date())
    _, sig_b64 = key.split(".", 1)
    import json
    forged = base64.urlsafe_b64encode(
        json.dumps({"email": "pirate@evil.com", "exp": future_date()}).encode()
    ).rstrip(b"=").decode()
    status = check_license(make_settings(f"SIMON-pro-{forged}.{sig_b64}", require=True))
    assert not status.valid


def test_expired_key_is_invalid_with_reason() -> None:
    """A well-signed but expired key is rejected and says why."""
    key = keygen.make_key("pro", "customer@x.com", past_date())
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


def test_require_license_or_exit_passes_on_valid() -> None:
    """The startup gate returns the status without exiting when valid."""
    key = keygen.make_key("pro", "customer@x.com", future_date())
    exits: list[int] = []
    status = require_license_or_exit(make_settings(key, require=True),
                                     exit_func=exits.append)
    assert exits == []
    assert isinstance(status, LicenseStatus)
    assert status.plan == "pro"


def test_real_settings_object_roundtrip() -> None:
    """check_license works against the real pydantic Settings class."""
    from simon.config import Settings
    key = keygen.make_key("business", "acme@corp.com", future_date())
    settings = Settings(simon_license_key=key, simon_require_license=True)
    status = check_license(settings)
    assert status.valid and status.plan == "business"
