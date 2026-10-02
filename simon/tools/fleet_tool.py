"""Fleet tools: customer provisioning, fleet status, health.

provision_customer is the Agent-Zero pipeline entry — mutating, and
approval-gated by policy.yml (the pipeline executes only after the owner's
explicit approval, with the parameter diff in the ask). The agent proposes
parameters into a signed template; it never writes IaC.
"""

from __future__ import annotations

import logging
import secrets
import shutil
import subprocess

from .. import provisioner
from .base import Tool

log = logging.getLogger(__name__)


def _az_available() -> bool:
    return shutil.which("az") is not None


def _execute_provision(fleet_id: int) -> str:
    """The pipeline. Runs ONLY after owner approval (the tool is gated).

    Deterministic steps; each writes a receipt; any failure parks the
    instance at a status the owner can act on. The agent's words never
    reach a shell.
    """
    rows = [r for r in provisioner.fleet_status() if r["id"] == fleet_id]
    if not rows:
        return f"Error: fleet entry #{fleet_id} not found"
    entry = rows[0]
    provisioner.set_status(fleet_id, "provisioning")

    if not _az_available():
        provisioner.set_status(
            fleet_id, "awaiting_credentials",
            "az CLI not installed on the control plane")
        return ("Provisioning paused, sir: the Azure CLI is not installed on "
                "this machine. Install it (`brew install azure-cli`), sign "
                "in (`az login`), and tell me to resume — the customer is "
                f"registered as fleet #{fleet_id} and the receipt chain is "
                "intact.")

    # Azure CLI present — check authentication before planning resources.
    check = subprocess.run(["az", "account", "show"], capture_output=True,
                           text=True, timeout=30)
    if check.returncode != 0:
        provisioner.set_status(fleet_id, "awaiting_credentials",
                               "az not authenticated")
        return ("The Azure CLI is installed but not signed in. Run "
                "`az login` and tell me to resume — fleet "
                f"#{fleet_id} is registered and waiting.")

    # Authenticated: create the resource group + VM from the template.
    import json as _json
    params = _json.loads(entry["params_json"])
    rg = f"simon-{params['customer']}"
    vm = f"simon-{params['customer']}-vm1"
    try:
        subprocess.run(
            ["az", "group", "create", "--name", rg,
             "--location", params["region"]],
            capture_output=True, text=True, timeout=120, check=True)
        create = subprocess.run(
            ["az", "vm", "create", "--resource-group", rg, "--name", vm,
             "--size", params["vm_size"], "--image",
             "Ubuntu2404", "--generate-ssh-keys", "--output", "json"],
            capture_output=True, text=True, timeout=600)
        if create.returncode != 0:
            raise RuntimeError(create.stderr[:300])
        info = _json.loads(create.stdout)
        ip = (info.get("publicIpAddress") or "").strip()
        token = secrets.token_urlsafe(24)
        provisioner.set_endpoint(fleet_id, ip or f"ssh:{vm}@{rg}", token)
        provisioner.set_status(fleet_id, "deployed",
                               f"vm {vm} at {ip or 'pending-ip'}")
        return (f"Deployed, sir: {vm} is live in {params['region']} "
                f"({ip or 'IP pending'}). Health attestation begins on first "
                f"heartbeat. Fleet #{fleet_id}, receipts chained.")
    except Exception as exc:  # noqa: BLE001
        provisioner.set_status(fleet_id, "failed", repr(exc)[:200])
        return (f"Error: provisioning failed for fleet #{fleet_id}: "
                f"{str(exc)[:200]}. The receipt chain shows exactly where; "
                "nothing was left half-created without a record.")


def _provision_customer(customer: str, template: str, region: str = "eastus",
                        vm_size: str = "Standard_B2s") -> str:
    params = {"customer": customer, "region": region, "vm_size": vm_size}
    fleet_id, err = provisioner.register_instance(customer, template, params)
    if err:
        return f"Error: {err}"
    return _execute_provision(fleet_id)


def _fleet_status() -> str:
    rows = provisioner.fleet_status()
    if not rows:
        return "No customer instances yet."
    lines = []
    for r in rows:
        seen = r.get("last_seen") or "never"
        lines.append(f"#{r['id']} {r['customer']} — {r['template']} — "
                     f"{r['status']} (health: {seen})")
    chain = "intact" if provisioner.verify_chain() else "BROKEN"
    return "Fleet:\n" + "\n".join(lines) + f"\nReceipt chain: {chain}"


def _customer_health(fleet_id: int) -> str:
    rows = [r for r in provisioner.fleet_status() if r["id"] == int(fleet_id)]
    if not rows:
        return f"Error: no fleet entry #{fleet_id}"
    r = rows[0]
    receipts = provisioner.receipts(int(fleet_id))
    lines = [f"#{r['id']} {r['customer']} — {r['status']}",
             f"endpoint: {r.get('endpoint') or '—'}",
             f"last heartbeat: {r.get('last_seen') or 'never'}",
             "receipts:"]
    lines += [f"  {x['created_at']} {x['event']} {x['detail'][:60]}"
              for x in receipts[-8:]]
    return "\n".join(lines)


def register_fleet_tools(registry, settings) -> None:
    registry.register(Tool(
        name="provision_customer",
        description="Provision a new customer's Simon instance from the "
                    "template catalog (owner approval required — the "
                    "pipeline runs only after explicit approval).",
        parameters={"type": "object", "properties": {
            "customer": {"type": "string",
                         "description": "Customer slug, DNS-safe"},
            "template": {"type": "string",
                         "description": "Template name (azure-vm-basic)"},
            "region": {"type": "string"},
            "vm_size": {"type": "string"}},
            "required": ["customer", "template"]},
        func=_provision_customer,
    ))
    registry.register(Tool(
        name="fleet_status",
        description="List customer instances, their status, and whether the "
                    "provisioning receipt chain is intact.",
        parameters={"type": "object", "properties": {}},
        func=lambda: _fleet_status(),
    ))
    registry.register(Tool(
        name="customer_health",
        description="One customer instance: status, endpoint, last "
                    "heartbeat, and its provisioning receipts.",
        parameters={"type": "object", "properties": {
            "fleet_id": {"type": "integer"}}, "required": ["fleet_id"]},
        func=lambda fleet_id: _customer_health(fleet_id),
    ))
