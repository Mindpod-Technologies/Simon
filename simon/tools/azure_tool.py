"""Azure tenant management integration for Simon (optional).

Registered by build_default_registry() only when SIMON_AZURE_ENABLED=true.
Implemented by shelling out to the Azure CLI (``az``): every command runs
with ``-o json`` and a timeout, output is JSON-parsed and truncated, and ALL
failures are returned as strings — these tools never raise.

Authentication is NOT handled here: Simon uses whatever identity ``az`` is
currently logged in as (interactive ``az login`` on a Mac, managed identity
or service principal on a VPS). Scope that identity's RBAC roles according
to the principle of least privilege (Reader for monitoring; add Virtual
Machine Contributor only if SIMON_AZURE_ALLOW_WRITE=true).
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess

from .base import Tool

log = logging.getLogger(__name__)

_TIMEOUT = 60
_MAX_OUTPUT = 6000
_MAX_RESOURCES = 50
_MAX_USERS = 25

_NOT_INSTALLED = (
    "Azure CLI not installed: install 'az' first — "
    "macOS: brew install azure-cli; "
    "Debian/Ubuntu: curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash — "
    "then run 'az login' (or 'az login --identity' on an Azure VM)."
)


def _az_available() -> bool:
    return shutil.which("az") is not None


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + "\n... (output truncated)"


def _run_az(args: list[str]) -> tuple[list | dict | None, str | None]:
    """Run ``az <args> -o json`` and return (parsed_json, error_message).

    Exactly one of the two return values is None. Never raises.
    """
    cmd = ["az", *args, "-o", "json"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, f"Error: az command timed out after {_TIMEOUT}s: {' '.join(cmd)}"
    except Exception as exc:  # noqa: BLE001 - never raise
        return None, f"Error: failed to run az: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return None, f"Error: az {' '.join(args)} failed: {_truncate(detail)}"
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError:
        # Some az commands emit no/invalid JSON on success (e.g. vm start).
        return proc.stdout.strip() or {}, None


def register_azure_tools(registry, settings) -> None:
    """Register Azure management tools on the given registry."""

    def azure_account() -> str:
        """Show the current Azure subscription and identity context."""
        if not _az_available():
            return _NOT_INSTALLED
        account, err = _run_az(["account", "show"])
        if err:
            return err + " (is 'az login' done?)"
        lines = [
            f"Subscription: {account.get('name', '?')} ({account.get('id', '?')})",
            f"Tenant: {account.get('tenantId', '?')}",
            f"State: {account.get('state', '?')}",
        ]
        user = account.get("user") or {}
        lines.append(f"Identity: {user.get('name', '?')} ({user.get('type', '?')})")
        # signed-in-user fails for service principals / managed identities — tolerate.
        sp_user, sp_err = _run_az(["ad", "signed-in-user", "show"])
        if sp_err is None and isinstance(sp_user, dict):
            lines.append(
                f"Signed-in user: {sp_user.get('displayName', '?')} "
                f"<{sp_user.get('userPrincipalName', '?')}>"
            )
        return "\n".join(lines)

    def azure_list_resources(resource_group: str = "", resource_type: str = "") -> str:
        """List Azure resources, optionally filtered by resource group / type."""
        if not _az_available():
            return _NOT_INSTALLED
        args = ["resource", "list"]
        if resource_group:
            args += ["--resource-group", resource_group]
        if resource_type:
            args += ["--resource-type", resource_type]
        data, err = _run_az(args)
        if err:
            return err
        if not data:
            return "No resources found."
        lines = []
        for r in data[:_MAX_RESOURCES]:
            name = r.get("name", "?")
            rtype = r.get("type", "?")
            rg = r.get("resourceGroup", "?")
            loc = r.get("location", "?")
            status = (r.get("tags") or {}).get("status", "")
            line = f"{name} | {rtype} | {rg} | {loc}"
            if status:
                line += f" | status={status}"
            lines.append(line)
        if len(data) > _MAX_RESOURCES:
            lines.append(f"... and {len(data) - _MAX_RESOURCES} more (capped at {_MAX_RESOURCES}).")
        return _truncate("\n".join(lines))

    def azure_list_resource_groups() -> str:
        """List resource groups with location and provisioning state."""
        if not _az_available():
            return _NOT_INSTALLED
        data, err = _run_az(["group", "list"])
        if err:
            return err
        if not data:
            return "No resource groups found."
        lines = [
            f"{g.get('name', '?')} | {g.get('location', '?')} | "
            f"{(g.get('properties') or {}).get('provisioningState', '?')}"
            for g in data
        ]
        return _truncate("\n".join(lines))

    def azure_vm_status(resource_group: str = "") -> str:
        """Show VM power states (optionally filtered by resource group)."""
        if not _az_available():
            return _NOT_INSTALLED
        args = ["vm", "list", "-d"]
        if resource_group:
            args += ["--resource-group", resource_group]
        data, err = _run_az(args)
        if err:
            return err
        if not data:
            return "No VMs found."
        lines = []
        for vm in data:
            line = (
                f"{vm.get('name', '?')} | {vm.get('resourceGroup', '?')} | "
                f"{vm.get('hardwareProfile', {}).get('vmSize', '?')} | "
                f"{vm.get('powerState', '?')}"
            )
            ips = vm.get("publicIps") or ""
            if ips:
                line += f" | public IP: {ips}"
            lines.append(line)
        return _truncate("\n".join(lines))

    def _vm_power(action: str, verb: str) -> None:
        """Build a VM power-action tool function."""

        def do_action(name: str, resource_group: str) -> str:
            """Run the power action against the named VM."""
            if not _az_available():
                return _NOT_INSTALLED
            _, err = _run_az(["vm", action, "--name", name, "--resource-group", resource_group])
            if err:
                return err
            return f"Done, sir — VM '{name}' in '{resource_group}' {verb} requested successfully."

        return do_action

    def azure_costs() -> str:
        """Current-month actual cost grouped by service (Cost Management)."""
        if not _az_available():
            return _NOT_INSTALLED
        import datetime as _dt

        today = _dt.date.today()
        start = today.replace(day=1).isoformat()
        end = today.isoformat()
        data, err = _run_az(
            ["costmanagement", "query", "--type", "Usage",
             "--timeframe", "Custom", "--time-period", f"from={start},to={end}",
             "--dataset-aggregation", '{"totalCost":{"name":"PreTaxCost","function":"Sum"}}',
             "--dataset-grouping", '[{"type":"Dimension","name":"ServiceName"}]'],
        )
        if err is None and isinstance(data, dict):
            rows = ((data.get("properties") or {}).get("rows")) or []
            if rows:
                lines = [f"Azure spend {start} → {end} (actual, by service):"]
                for row in rows:
                    cost, service = row[0], row[1]
                    try:
                        cost = f"{float(cost):.2f}"
                    except (TypeError, ValueError):
                        pass
                    lines.append(f"{service}: {cost}")
                return _truncate("\n".join(lines))
        # Fallback for older CLI without costmanagement: consumption summary.
        log.info("costmanagement query unavailable (%s), falling back to consumption", err)
        data2, err2 = _run_az(
            ["consumption", "usage", "list", "--start-date", start, "--end-date", end]
        )
        if err2 is None and isinstance(data2, list):
            totals: dict[str, float] = {}
            for item in data2:
                service = item.get("meterDetails", {}).get("meterCategory", "other") \
                    or item.get("consumedService", "other")
                try:
                    totals[service] = totals.get(service, 0.0) + float(item.get("pretaxCost", 0) or 0)
                except (TypeError, ValueError):
                    continue
            if totals:
                lines = [f"Azure usage cost {start} → {end} (consumption API, approximate):"]
                lines += [f"{svc}: {amt:.2f}" for svc, amt in sorted(totals.items())]
                return _truncate("\n".join(lines))
        return (
            "Error: could not retrieve cost data. The 'costmanagement' and "
            "'consumption' CLI commands both failed. Ensure the Cost Management "
            "Reader role is assigned, or check costs in the Azure Portal."
        )

    def azure_entra_users(filter: str = "") -> str:
        """List Entra ID (Azure AD) users, optionally filtered by name substring."""
        if not _az_available():
            return _NOT_INSTALLED
        args = ["ad", "user", "list"]
        if filter:
            args += ["--filter", f"startswith(displayName,'{filter}')"]
        data, err = _run_az(args)
        if err:
            return err
        if not data:
            return "No users found."
        if filter:  # server-side startswith only; apply substring match client-side too
            needle = filter.lower()
            data = [u for u in data if needle in (u.get("displayName") or "").lower()] or data
        lines = [
            f"{u.get('displayName', '?')} | {u.get('userPrincipalName', '?')} | {u.get('id', '?')}"
            for u in data[:_MAX_USERS]
        ]
        if len(data) > _MAX_USERS:
            lines.append(f"... and {len(data) - _MAX_USERS} more (capped at {_MAX_USERS}).")
        return _truncate("\n".join(lines))

    def azure_entra_group_members(group: str) -> str:
        """List members of an Entra ID group (by object id or display name)."""
        if not _az_available():
            return _NOT_INSTALLED
        group_id = group
        # If it doesn't look like a GUID, resolve the display name to an id.
        if not all(c in "0123456789abcdef-" for c in group.lower()):
            grp, err = _run_az(["ad", "group", "show", "--group", group])
            if err:
                return err
            group_id = grp.get("id", group)
        data, err = _run_az(["ad", "group", "member", "list", "--group", group_id])
        if err:
            return err
        if not data:
            return f"Group '{group}' has no members (or none visible)."
        lines = [
            f"{m.get('displayName', '?')} | {m.get('userPrincipalName', m.get('appId', ''))} | "
            f"{m.get('id', '?')}"
            for m in data[:_MAX_RESOURCES]
        ]
        return _truncate("\n".join(lines))

    def azure_resource_health() -> str:
        """Recent notable Azure activity-log events (last 10)."""
        if not _az_available():
            return _NOT_INSTALLED
        data, err = _run_az(
            ["monitor", "activity-log", "list", "--max-events", "10",
             "--status", "Failed"]
        )
        if err or not data:
            # Tolerate: fall back to unfiltered recent events.
            data, err2 = _run_az(["monitor", "activity-log", "list", "--max-events", "10"])
            if err2:
                return (
                    "Error: could not retrieve activity log / resource health events. "
                    "Ensure the identity has Reader + Monitoring Reader on the subscription."
                )
        if not data:
            return "No recent notable activity-log events."
        lines = []
        for e in data:
            when = e.get("eventTimestamp", "?")
            status = (e.get("status") or {}).get("value", "?")
            op = (e.get("operationName") or {}).get("localizedValue", "?")
            caller = e.get("caller", "?")
            lines.append(f"{when} | {status} | {op} | {caller}")
        return _truncate("\n".join(lines))

    registry.register(Tool(
        name="azure_account",
        description=(
            "Show the current Azure subscription, tenant, and signed-in "
            "identity context (what 'az' is authenticated as)."
        ),
        parameters={"type": "object", "properties": {}},
        func=azure_account,
    ))
    registry.register(Tool(
        name="azure_list_resources",
        description=(
            "List Azure resources in the subscription, optionally filtered by "
            "resource group and/or resource type (e.g. 'Microsoft.Compute/virtualMachines')."
        ),
        parameters={
            "type": "object",
            "properties": {
                "resource_group": {"type": "string", "description": "Optional resource group filter."},
                "resource_type": {"type": "string", "description": "Optional ARM resource type filter."},
            },
        },
        func=azure_list_resources,
    ))
    registry.register(Tool(
        name="azure_list_resource_groups",
        description="List Azure resource groups with location and provisioning state.",
        parameters={"type": "object", "properties": {}},
        func=azure_list_resource_groups,
    ))
    registry.register(Tool(
        name="azure_vm_status",
        description=(
            "Show Azure VMs with power state, size, and public IP; optionally "
            "filter by resource group."
        ),
        parameters={
            "type": "object",
            "properties": {
                "resource_group": {"type": "string", "description": "Optional resource group filter."},
            },
        },
        func=azure_vm_status,
    ))
    registry.register(Tool(
        name="azure_costs",
        description=(
            "Show this month's actual Azure spend grouped by service "
            "(Cost Management; falls back to consumption summary)."
        ),
        parameters={"type": "object", "properties": {}},
        func=azure_costs,
    ))
    registry.register(Tool(
        name="azure_entra_users",
        description="List Entra ID (Azure AD) users; optional display-name substring filter.",
        parameters={
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optional display-name substring filter."},
            },
        },
        func=azure_entra_users,
    ))
    registry.register(Tool(
        name="azure_entra_group_members",
        description="List members of an Entra ID group by object id or display name.",
        parameters={
            "type": "object",
            "properties": {
                "group": {"type": "string", "description": "Group object id or display name."},
            },
            "required": ["group"],
        },
        func=azure_entra_group_members,
    ))
    registry.register(Tool(
        name="azure_resource_health",
        description="Show recent notable Azure activity-log / health events (last 10).",
        parameters={"type": "object", "properties": {}},
        func=azure_resource_health,
    ))

    if getattr(settings, "simon_azure_allow_write", False):
        safety = (
            " WRITE ACTION — this changes the tenant. Confirm with the user "
            "before calling (per Simon's safety rules)."
        )
        for action, tool_name, desc, verb in [
            ("start", "azure_vm_start", "Start a stopped/deallocated Azure VM.", "start"),
            ("deallocate", "azure_vm_stop",
             "Stop (deallocate) an Azure VM — stops compute billing.", "deallocate"),
            ("restart", "azure_vm_restart", "Restart an Azure VM.", "restart"),
        ]:
            registry.register(Tool(
                name=tool_name,
                description=desc + safety,
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "VM name."},
                        "resource_group": {"type": "string", "description": "Resource group of the VM."},
                    },
                    "required": ["name", "resource_group"],
                },
                func=_vm_power(action, verb),
            ))
        log.info("Azure write tools registered (SIMON_AZURE_ALLOW_WRITE=true).")

    log.info("Azure tools registered.")
