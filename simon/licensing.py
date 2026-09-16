"""Offline license-key validation for commercial deployments of Simon.

License keys are Ed25519-signed, offline-verifiable tokens of the form::

    SIMON-<plan>-<payload_b64url>.<sig_b64url>

where ``payload_b64url`` is the base64url encoding of a JSON object
``{"email": ..., "exp": "YYYY-MM-DD" or ""}`` and ``sig_b64url`` is the
base64url encoding of the Ed25519 signature over the ASCII string
``"<plan>.<payload_b64url>"``. Only the vendor's PUBLIC key is embedded here;
the private signing key lives outside this repository (vendor-only keygen).

Design notes:

* Verification is fully offline — no phone-home, by design (see COMMERCIAL.md).
* This module imports cleanly even when ``cryptography`` is not installed;
  in that case it degrades to a "trial" status with a logged note so that
  open-source / personal use is never broken by a missing optional dependency.
* :func:`check_license` never raises; it always returns a
  :class:`LicenseStatus`.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Vendor Ed25519 PUBLIC key (raw 32 bytes, hex). The matching private key is
# kept OUTSIDE this repo (vendor-only keygen) — never ship it. Rotated
# 2026-09-16 before the repo went public; the previous pair was retired.
_VENDOR_PUBLIC_KEY_HEX = "19ba4f202833cc247d8c7d0d07aefae390381142542190d1af782ba96021dad6"

VALID_PLANS = ("trial", "pro", "business")

try:  # Optional dependency — OSS installs without it must keep working.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )

    _HAS_CRYPTOGRAPHY = True
except ImportError:  # pragma: no cover - exercised only without cryptography
    Ed25519PublicKey = None  # type: ignore[assignment]
    _HAS_CRYPTOGRAPHY = False
    logger.info(
        "cryptography package not installed; license verification disabled, "
        "falling back to trial plan. Install `cryptography` to validate keys."
    )


@dataclass
class LicenseStatus:
    """Result of a license check.

    ``plan`` is one of ``"trial"``, ``"pro"``, ``"business"``. ``expires``
    is an ISO date string (``YYYY-MM-DD``) or ``""`` for non-expiring keys.
    ``reason`` is a human-readable explanation, chiefly when ``valid`` is
    false.
    """

    valid: bool
    plan: str = "trial"
    email: str = ""
    expires: str = ""
    reason: str = ""


def _b64url_decode(data: str) -> bytes:
    """Decode base64url, tolerating missing padding."""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _parse_key(key: str) -> tuple[str, dict, bytes]:
    """Split and decode a license key; raises ValueError on any malformation."""
    prefix, _, rest = key.partition(".")
    if not rest:
        raise ValueError("missing signature component")
    parts = prefix.split("-", 2)
    if len(parts) != 3 or parts[0] != "SIMON":
        raise ValueError("keys must look like SIMON-<plan>-<payload>.<sig>")
    plan = parts[1].lower()
    if plan not in VALID_PLANS:
        raise ValueError(f"unknown plan {plan!r}")
    payload_b64 = parts[2]
    if not payload_b64:
        raise ValueError("empty payload")
    try:
        payload = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(rest)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError(f"undecodable key material: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("payload is not a JSON object")
    return plan, payload, signature


def _verify_signature(plan: str, key: str, signature: bytes) -> bool:
    """Verify the Ed25519 signature over ``<plan>.<payload_b64url>``."""
    if not _HAS_CRYPTOGRAPHY:
        return False
    payload_b64 = key.split(".", 1)[0].split("-", 2)[2]
    signed = f"{plan}.{payload_b64}".encode("ascii")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(_VENDOR_PUBLIC_KEY_HEX)
        )
        public_key.verify(signature, signed)
    except Exception:  # InvalidSignature, malformed key bytes, etc.
        return False
    return True


def check_license(settings: Any) -> LicenseStatus:
    """Validate the configured license key. Never raises.

    Rules:

    * Empty key and ``simon_require_license`` false (the default) → a valid
      ``trial`` status, so self-hosted personal use is unaffected.
    * ``simon_require_license`` true with an empty, malformed, tampered, or
      expired key → an invalid status with an explanatory ``reason``.
    * A structurally valid key is checked against the embedded vendor public
      key; ``exp`` (when set) must be today or later.
    """
    try:
        key = (getattr(settings, "simon_license_key", "") or "").strip()
        required = bool(getattr(settings, "simon_require_license", False))

        if not key:
            if required:
                return LicenseStatus(
                    valid=False,
                    reason="SIMON_REQUIRE_LICENSE=true but SIMON_LICENSE_KEY "
                           "is empty; purchase a key or disable the requirement.",
                )
            return LicenseStatus(valid=True, plan="trial",
                                 reason="no license key; running as trial")

        try:
            plan, payload, signature = _parse_key(key)
        except ValueError as exc:
            return LicenseStatus(valid=False, reason=f"malformed license key: {exc}")

        email = str(payload.get("email", ""))
        expires = str(payload.get("exp", "") or "")

        if not _verify_signature(plan, key, signature):
            if not _HAS_CRYPTOGRAPHY:
                # Degrade gracefully: without cryptography we cannot verify, so
                # we do not lock anyone out — report an unverified trial.
                logger.warning(
                    "cryptography unavailable; cannot verify license key, "
                    "continuing on trial plan."
                )
                return LicenseStatus(valid=True, plan="trial", email=email,
                                     reason="license verification unavailable "
                                            "(cryptography not installed)")
            return LicenseStatus(valid=False, plan=plan, email=email,
                                 expires=expires,
                                 reason="signature verification failed; key is "
                                        "invalid or tampered")

        if expires:
            try:
                exp_date = date.fromisoformat(expires)
            except ValueError:
                return LicenseStatus(valid=False, plan=plan, email=email,
                                     reason=f"invalid expiry date {expires!r}")
            if exp_date < date.today():
                return LicenseStatus(valid=False, plan=plan, email=email,
                                     expires=expires,
                                     reason=f"license expired on {expires}")

        return LicenseStatus(valid=True, plan=plan, email=email,
                             expires=expires,
                             reason="license valid" if required else "")
    except Exception as exc:  # absolute safety net: never raise
        logger.exception("unexpected error during license check")
        return LicenseStatus(valid=False, reason=f"license check error: {exc}")


def require_license_or_exit(settings: Any,
                            exit_func: Optional[Any] = None) -> LicenseStatus:
    """Enforce licensing at startup; exit politely if the status is invalid.

    When the status is valid, logs the active plan and returns it. When
    invalid, prints a polite message to stderr and exits with status 1
    (``exit_func`` may be injected by tests; defaults to ``sys.exit``).
    """
    status = check_license(settings)
    if status.valid:
        logger.info("License OK: plan=%s email=%s expires=%s",
                    status.plan, status.email or "-", status.expires or "-")
        return status
    print(
        "\nSimon could not validate a commercial license.\n"
        f"  Reason: {status.reason}\n\n"
        "  - Personal/self-hosted use: leave SIMON_REQUIRE_LICENSE=false "
        "(the default)\n"
        "    and no key is needed.\n"
        "  - Commercial use: set SIMON_LICENSE_KEY to the key you received "
        "at purchase.\n"
        "See COMMERCIAL.md for details.\n",
        file=sys.stderr,
    )
    (exit_func or sys.exit)(1)
    return status  # unreachable with sys.exit, handy for injected exit_func
