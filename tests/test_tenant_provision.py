"""Simon Cloud tenant provisioning: render, assign, idempotency, lifecycle."""

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from billing import server
from deploy.provision_tenant import Provisioner, slugify
from simon import licensing


@pytest.fixture()
def cloud(tmp_path, monkeypatch):
    priv = Ed25519PrivateKey.generate()
    monkeypatch.setenv("SIMON_LICENSE_PRIVATE_KEY",
                       priv.private_bytes_raw().hex())
    monkeypatch.setattr(licensing, "_VENDOR_PUBLIC_KEY_HEX",
                        priv.public_key().public_bytes_raw().hex())
    monkeypatch.setenv("SIMON_CLOUD_DOMAIN", "simonwork.app")
    monkeypatch.setenv("SIMON_CLOUD_DRY_RUN", "1")  # never touch docker
    return Provisioner(dry_run=True, root=str(tmp_path / "tenants"))


def test_slugify_uses_domain():
    assert slugify("owner@acme-corp.com") == "acme-corp"
    assert slugify("a@b.co") == "b"


def test_provision_renders_isolated_tenant(cloud, tmp_path):
    record = cloud.provision("ops@acme.com", "pro")
    assert record["url"] == "https://acme.simonwork.app"
    d = tmp_path / "tenants" / "acme"
    env = (d / ".env").read_text()
    assert "SIMON_REQUIRE_LICENSE=true" in env
    assert "SIMON_APPROVALS_ENABLED=true" in env
    assert "SIMON_ALLOW_SHELL=false" in env
    assert "SIMON_LICENSE_KEY=SIMON-pro-" in env
    assert "{{" not in env  # every placeholder rendered
    compose = (d / "docker-compose.yml").read_text()
    assert f"127.0.0.1:{record['port']}:8788" in compose
    assert "no-new-privileges" in compose
    caddy = (d / "Caddyfile").read_text()
    assert "acme.simonwork.app" in caddy
    assert str(record["port"]) in caddy


def test_provisioned_license_key_is_valid(cloud):
    record = cloud.provision("ops@acme.com", "pro")
    env = (__import__("pathlib").Path(record["dir"]) / ".env").read_text()
    key = next(l.split("=", 1)[1] for l in env.splitlines()
               if l.startswith("SIMON_LICENSE_KEY="))

    class S:
        simon_license_key = key
        simon_require_license = True

    status = licensing.check_license(S())
    assert status.valid and status.plan == "pro" and status.email == "ops@acme.com"


def test_unique_ports_and_idempotency(cloud):
    a = cloud.provision("ops@acme.com", "pro")
    b = cloud.provision("it@bravo.com", "business")
    assert a["port"] != b["port"]
    again = cloud.provision("ops@acme.com", "pro")
    assert again["note"] == "already provisioned"
    assert again["port"] == a["port"]


def test_list_stop_delete(cloud):
    cloud.provision("ops@acme.com", "pro")
    assert len(cloud.list()) == 1
    cloud.stop("acme")            # dry-run: no docker call
    cloud.delete("acme")          # removes dir + state
    assert cloud.list() == []
    with pytest.raises(ValueError):
        cloud.stop("acme")


def test_bad_input_rejected(cloud):
    with pytest.raises(ValueError):
        cloud.provision("not-an-email", "pro")
    with pytest.raises(ValueError):
        cloud.provision("ops@acme.com", "enterprise")


def test_fulfill_provisions_when_enabled(cloud, tmp_path, monkeypatch):
    """Webhook + SIMON_CLOUD_PROVISION=1 → tenant spins up with the key."""
    monkeypatch.setenv("SIMON_CLOUD_PROVISION", "1")
    monkeypatch.setenv("SIMON_CLOUD_ROOT", str(tmp_path / "tenants"))
    monkeypatch.setenv("SIMON_CLOUD_DRY_RUN", "1")
    monkeypatch.setenv("STRIPE_PAYMENT_LINK_PLANS", "plink_pro=pro")
    db = str(tmp_path / "billing.db")
    record = server.fulfill(
        {"id": "cs_hosted_1",
         "customer_details": {"email": "ops@acme.com"},
         "payment_link": "plink_pro", "amount_total": 1900},
        db_path=db)
    assert record["tenant_url"] == "https://acme.simonwork.app"
    assert (tmp_path / "tenants" / "acme" / ".env").exists()
