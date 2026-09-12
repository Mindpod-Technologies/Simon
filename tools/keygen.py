#!/usr/bin/env python3
"""Simon license key generator (vendor-side tool).

Usage::

    python tools/keygen.py --plan pro --email customer@x.com --exp 2027-01-01
    python tools/keygen.py --plan business --email acme@corp.com        # no expiry
    python tools/keygen.py --rotate                                     # new keypair

``--rotate`` prints a fresh Ed25519 keypair: embed the PUBLIC key in
``simon/licensing.py`` (``_VENDOR_PUBLIC_KEY_HEX``) and replace the PRIVATE
key below.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys

# ======================================================================
# !!!  DO NOT SHIP TO CUSTOMERS — KEEP SERVER-SIDE ONLY  !!!
#
# This is the vendor's Ed25519 PRIVATE signing key. Anyone holding it can
# mint valid Simon license keys. Never commit it to a public repo, never
# include it in customer distributions, docker images, or release tarballs.
# Only the PUBLIC key (embedded in simon/licensing.py) may be distributed.
# ======================================================================
_VENDOR_PRIVATE_KEY_HEX = "c78618df82a9e9e79488ca725be27cb95f4a723243c55c17f0123a7c45da0ab6"

VALID_PLANS = ("trial", "pro", "business")


def _b64url_encode(data: bytes) -> str:
    """Encode base64url without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_key(plan: str, email: str, exp: str,
             private_key_hex: str = _VENDOR_PRIVATE_KEY_HEX) -> str:
    """Create a signed ``SIMON-<plan>-<payload>.<sig>`` license key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    if plan not in VALID_PLANS:
        raise ValueError(f"plan must be one of {VALID_PLANS}")
    private_key = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(private_key_hex)
    )
    payload = {"email": email, "exp": exp}
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    signed = f"{plan}.{payload_b64}".encode("ascii")
    sig_b64 = _b64url_encode(private_key.sign(signed))
    return f"SIMON-{plan}-{payload_b64}.{sig_b64}"


def rotate() -> None:
    """Print a fresh Ed25519 keypair and where each half goes."""
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    key = Ed25519PrivateKey.generate()
    priv = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    print("Fresh Ed25519 keypair:")
    print(f"  PUBLIC  (embed in simon/licensing.py _VENDOR_PUBLIC_KEY_HEX):\n    {pub.hex()}")
    print(f"  PRIVATE (embed HERE as _VENDOR_PRIVATE_KEY_HEX — DO NOT SHIP):\n    {priv.hex()}")
    print("\nExisting keys stop validating as soon as you rotate; reissue them.")


def main() -> None:
    """Parse arguments and print a license key (or rotate the keypair)."""
    parser = argparse.ArgumentParser(
        prog="keygen",
        description="Generate signed Simon license keys (vendor-side only).",
    )
    parser.add_argument("--plan", choices=VALID_PLANS,
                        help="license tier to issue")
    parser.add_argument("--email", default="", help="customer email")
    parser.add_argument("--exp", default="",
                        help="expiry date YYYY-MM-DD; empty = never expires")
    parser.add_argument("--rotate", action="store_true",
                        help="print a fresh keypair instead of issuing a key")
    args = parser.parse_args()

    if args.rotate:
        rotate()
        return
    if not args.plan:
        parser.error("--plan is required unless --rotate is given")
    if args.exp:
        # Validate the date early so a typo never ships to a customer.
        from datetime import date
        try:
            date.fromisoformat(args.exp)
        except ValueError:
            parser.error(f"--exp must be YYYY-MM-DD, got {args.exp!r}")

    try:
        print(make_key(args.plan, args.email, args.exp))
    except ImportError:
        print("error: the 'cryptography' package is required "
              "(pip install cryptography)", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
