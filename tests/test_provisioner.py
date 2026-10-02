"""Simon Provisioner: template catalog, fleet registry, receipts chain,
approval-gated pipeline."""

from __future__ import annotations

import pytest

from simon import memory, provisioner


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    memory.init_db()
    provisioner.init_db()
    return tmp_path


def test_template_catalog_loads():
    templates = provisioner.list_templates()
    names = [t["name"] for t in templates]
    assert "azure-vm-basic" in names
    tpl = next(t for t in templates if t["name"] == "azure-vm-basic")
    assert "customer" in tpl["params"] and tpl["sha"]


def test_param_validation(db):
    assert provisioner.validate_params("azure-vm-basic", {
        "customer": "acme-co", "region": "eastus",
        "vm_size": "Standard_B2s"}) is None
    assert provisioner.validate_params("azure-vm-basic", {
        "customer": "BAD SLUG!!", "region": "eastus",
        "vm_size": "Standard_B2s"})
    assert provisioner.validate_params("azure-vm-basic", {
        "region": "eastus", "vm_size": "Standard_B2s"})
    assert provisioner.validate_params("nope-template", {})


def test_register_and_status(db):
    fleet_id, err = provisioner.register_instance(
        "acme-co", "azure-vm-basic",
        {"customer": "acme-co", "region": "eastus", "vm_size": "Standard_B2s"})
    assert err is None and fleet_id
    rows = provisioner.fleet_status()
    assert rows[0]["customer"] == "acme-co"
    assert rows[0]["status"] == "pending_approval"


def test_receipt_chain_is_tamper_evident(db):
    fid, _ = provisioner.register_instance(
        "acme-co", "azure-vm-basic",
        {"customer": "acme-co", "region": "eastus", "vm_size": "Standard_B2s"})
    provisioner.set_status(fid, "provisioning")
    provisioner.set_status(fid, "deployed", "vm live at 1.2.3.4")
    assert provisioner.verify_chain()
    rec = provisioner.receipts(fid)
    assert [r["event"] for r in rec] == [
        "registered", "status:provisioning", "status:deployed"]
    # Tamper: break the chain, detect it.
    conn = memory._connect()
    conn.execute("UPDATE provision_receipts SET prev_hash = 'X' WHERE id = 2")
    conn.commit()
    conn.close()
    assert not provisioner.verify_chain()


def test_heartbeat_requires_valid_token(db):
    fid, _ = provisioner.register_instance(
        "acme-co", "azure-vm-basic",
        {"customer": "acme-co", "region": "eastus", "vm_size": "Standard_B2s"})
    provisioner.set_endpoint(fid, "1.2.3.4", "token-abc")
    assert not provisioner.heartbeat(fid, "wrong-token")
    assert provisioner.heartbeat(fid, "token-abc")
    row = [r for r in provisioner.fleet_status() if r["id"] == fid][0]
    assert row["status"] == "pending_approval"  # not yet deployed
    assert row["last_seen"]


def test_heartbeat_marks_deployed_healthy(db):
    fid, _ = provisioner.register_instance(
        "acme-co", "azure-vm-basic",
        {"customer": "acme-co", "region": "eastus", "vm_size": "Standard_B2s"})
    provisioner.set_endpoint(fid, "1.2.3.4", "token-abc")
    provisioner.set_status(fid, "deployed")
    assert provisioner.heartbeat(fid, "token-abc")
    row = [r for r in provisioner.fleet_status() if r["id"] == fid][0]
    assert row["status"] == "healthy"


def test_pipeline_pauses_cleanly_without_azure(db, monkeypatch):
    """No az CLI on the control plane → the customer is registered, the
    status says exactly what's needed, and the receipt chain records it."""
    from simon.tools.fleet_tool import _provision_customer
    monkeypatch.setattr("simon.tools.fleet_tool._az_available",
                        lambda: False)
    out = _provision_customer("acme-co", "azure-vm-basic")
    assert "Azure CLI is not installed" in out
    row = provisioner.fleet_status()[0]
    assert row["status"] == "awaiting_credentials"
    assert provisioner.verify_chain()


def test_provision_customer_gated_by_policy():
    from simon import policy
    policy.reload()
    r = policy.evaluate("provision_customer",
                        {"customer": "acme-co", "template": "azure-vm-basic"})
    assert r["verdict"] == "approve"
    assert "acme-co" in r["reason"]
    policy.reload()
