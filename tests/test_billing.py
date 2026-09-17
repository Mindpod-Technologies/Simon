"""Billing: Stripe webhook → signed license key → customer email.

End-to-end proof that a paid checkout session produces a license key that
Simon's own licensing module validates — with a throwaway keypair, never
the vendor's real one.
"""

import hashlib
import hmac
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from billing import keys, server, store
from simon import licensing


@pytest.fixture()
def keypair(monkeypatch):
    """Throwaway Ed25519 pair; licensing verifies against its public half."""
    private = Ed25519PrivateKey.generate()
    priv_hex = private.private_bytes_raw().hex()
    pub_hex = private.public_key().public_bytes_raw().hex()
    monkeypatch.setenv("SIMON_LICENSE_PRIVATE_KEY", priv_hex)
    monkeypatch.setattr(licensing, "_VENDOR_PUBLIC_KEY_HEX", pub_hex)
    return priv_hex


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "billing.db")


def _session(session_id="cs_test_1", email="buyer@example.com",
             link="plink_pro", amount=1900):
    return {"id": session_id,
            "customer_details": {"email": email},
            "payment_link": link,
            "amount_total": amount}


def test_minted_key_validates_with_simon_licensing(keypair):
    key = keys.make_key("pro", "buyer@example.com", "2099-01-01")

    class S:
        simon_license_key = key
        simon_require_license = True

    status = licensing.check_license(S())
    assert status.valid, status.reason
    assert status.plan == "pro"
    assert status.email == "buyer@example.com"


def test_expired_key_rejected(keypair):
    key = keys.make_key("pro", "buyer@example.com", "2020-01-01")

    class S:
        simon_license_key = key
        simon_require_license = True

    assert not licensing.check_license(S()).valid


def test_expiry_policy():
    from datetime import date
    assert keys.expiry_for("pro", today=date(2026, 1, 1)) == "2027-01-01"
    assert keys.expiry_for("business") == ""


def test_missing_private_key_raises(monkeypatch):
    monkeypatch.delenv("SIMON_LICENSE_PRIVATE_KEY", raising=False)
    with pytest.raises(RuntimeError):
        keys.make_key("pro", "a@b.c", "")


def test_plan_mapping(monkeypatch):
    monkeypatch.setenv("STRIPE_PAYMENT_LINK_PLANS",
                       "plink_pro=pro,plink_biz=business")
    assert server.plan_for_session(_session(link="plink_pro")) == "pro"
    assert server.plan_for_session(_session(link="plink_biz")) == "business"
    # amount fallback when the link id is unknown
    assert server.plan_for_session(_session(link="x", amount=50000)) == "business"
    assert server.plan_for_session(_session(link="x", amount=1900)) == "pro"


def test_fulfill_mints_stores_emails(keypair, db):
    sent = []
    record = server.fulfill(_session(), db_path=db,
                            mailer=lambda to, subj, body: sent.append((to, body)))
    assert record["plan"] == "pro"
    assert record["license_key"].startswith("SIMON-pro-")
    assert sent and sent[0][0] == "buyer@example.com"
    assert record["license_key"] in sent[0][1]
    stored = store.lookup("cs_test_1", path=db)
    assert stored["emailed"] == 1


def test_fulfill_is_idempotent(keypair, db):
    sent = []
    mailer = lambda to, subj, body: sent.append(to)
    server.fulfill(_session(), db_path=db, mailer=mailer)
    again = server.fulfill(_session(), db_path=db, mailer=mailer)
    assert len(sent) == 1  # Stripe retries must not re-email
    assert again["stripe_session"] == "cs_test_1"


def _signed_client(monkeypatch, db, secret="whsec_test"):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    app = server.create_app(settings=None, db_path=db)
    return TestClient(app), secret


def _sign(payload: bytes, secret: str) -> str:
    t = int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + payload,
                   hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def test_webhook_end_to_end(keypair, monkeypatch, db):
    client, secret = _signed_client(monkeypatch, db)
    event = {"type": "checkout.session.completed",
             "data": {"object": _session()}}
    payload = json.dumps(event).encode()
    r = client.post("/stripe/webhook", content=payload,
                    headers={"stripe-signature": _sign(payload, secret)})
    assert r.status_code == 200
    assert r.json()["issued"] == "pro"
    assert store.lookup("cs_test_1", path=db)["email"] == "buyer@example.com"


def test_webhook_rejects_bad_signature(keypair, monkeypatch, db):
    client, _ = _signed_client(monkeypatch, db)
    r = client.post("/stripe/webhook", content=b"{}",
                    headers={"stripe-signature": "t=1,v1=bogus"})
    assert r.status_code == 400


def test_webhook_ignores_other_events(keypair, monkeypatch, db):
    client, secret = _signed_client(monkeypatch, db)
    payload = json.dumps({"type": "payment_intent.created"}).encode()
    r = client.post("/stripe/webhook", content=payload,
                    headers={"stripe-signature": _sign(payload, secret)})
    assert r.status_code == 200 and "ignored" in r.json()


def test_success_page_shows_key_then_polls(keypair, monkeypatch, db):
    client, _ = _signed_client(monkeypatch, db)
    # Unknown session → polling page
    r = client.get("/success?session_id=cs_unknown")
    assert r.status_code == 200 and "refresh" in r.text
    # Fulfilled session → key on the page
    server.fulfill(_session(), db_path=db)
    r = client.get("/success?session_id=cs_test_1")
    assert "SIMON-pro-" in r.text


def test_load_dotenv_strips_inline_comments(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "STRIPE_WEBHOOK_SECRET=whsec_abc  # from the dashboard\n"
        "QUOTED=\"keep # this\"\n"
        "export SKIPPED=1\n"
        "# full comment\n"
        "STRIPE_PAYMENT_LINK_PLANS=plink_1=pro,plink_2=business\n")
    for var in ("STRIPE_WEBHOOK_SECRET", "QUOTED", "SKIPPED",
                "STRIPE_PAYMENT_LINK_PLANS"):
        monkeypatch.delenv(var, raising=False)
    server._load_dotenv(str(env))
    import os
    assert os.environ["STRIPE_WEBHOOK_SECRET"] == "whsec_abc"
    assert os.environ["QUOTED"] == "keep # this"
    assert "SKIPPED" not in os.environ
    assert os.environ["STRIPE_PAYMENT_LINK_PLANS"] == "plink_1=pro,plink_2=business"
