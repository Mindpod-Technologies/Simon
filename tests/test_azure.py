"""Tests for Simon's Azure tools. No network and no real 'az' CLI required."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import simon.tools.azure_tool as azt
from simon.tools import build_default_registry


def make_settings(enabled: bool = True, allow_write: bool = False) -> SimpleNamespace:
    """Minimal Settings-compatible stub (matches simon.config.Settings fields)."""
    return SimpleNamespace(
        simon_workspace_dir="/tmp/simon-test",
        simon_allow_shell=False,
        imap_host="",
        smtp_host="",
        google_calendar_ics="",
        homeassistant_url="",
        homeassistant_token="",
        simon_azure_enabled=enabled,
        simon_azure_allow_write=allow_write,
    )


@pytest.fixture
def az_present(monkeypatch):
    """Pretend the az CLI is installed."""
    monkeypatch.setattr(azt.shutil, "which", lambda name: "/usr/bin/az" if name == "az" else None)


@pytest.fixture
def az_absent(monkeypatch):
    monkeypatch.setattr(azt.shutil, "which", lambda name: None)


def fake_completed(payload, returncode: int = 0, stderr: str = ""):
    out = json.dumps(payload) if not isinstance(payload, str) else payload
    return subprocess.CompletedProcess(args=["az"], returncode=returncode, stdout=out, stderr=stderr)


# ------------------------------------------------------------- registration

def test_azure_tools_not_registered_when_disabled():
    registry = build_default_registry(make_settings(enabled=False))
    assert "azure_account" not in registry
    assert "azure_vm_status" not in registry
    assert "azure_vm_start" not in registry


def test_azure_tools_registered_when_enabled(az_present):
    registry = build_default_registry(make_settings(enabled=True))
    for name in (
        "azure_account", "azure_list_resources", "azure_list_resource_groups",
        "azure_vm_status", "azure_costs", "azure_entra_users",
        "azure_entra_group_members", "azure_resource_health",
    ):
        assert name in registry, name


def test_write_tools_absent_when_allow_write_false(az_present):
    registry = build_default_registry(make_settings(enabled=True, allow_write=False))
    assert "azure_vm_start" not in registry
    assert "azure_vm_stop" not in registry
    assert "azure_vm_restart" not in registry


def test_write_tools_present_when_allow_write_true(az_present):
    registry = build_default_registry(make_settings(enabled=True, allow_write=True))
    assert "azure_vm_start" in registry
    assert "azure_vm_stop" in registry
    assert "azure_vm_restart" in registry


# ------------------------------------------------------------- az missing

def test_az_missing_returns_guidance(az_absent):
    registry = build_default_registry(make_settings())
    result = registry.call("azure_vm_status", {})
    assert result.startswith("Azure CLI not installed")


# ------------------------------------------------------------- happy paths

def test_list_resources_formats_table(az_present, monkeypatch):
    payload = [
        {"name": "vm1", "type": "Microsoft.Compute/virtualMachines",
         "resourceGroup": "rg-prod", "location": "uksouth", "tags": {"status": "prod"}},
        {"name": "store1", "type": "Microsoft.Storage/storageAccounts",
         "resourceGroup": "rg-data", "location": "eastus"},
    ]
    monkeypatch.setattr(azt.subprocess, "run", lambda *a, **k: fake_completed(payload))
    registry = build_default_registry(make_settings())
    result = registry.call("azure_list_resources", {"resource_group": "rg-prod"})
    assert "vm1 | Microsoft.Compute/virtualMachines | rg-prod | uksouth | status=prod" in result
    assert "store1 | Microsoft.Storage/storageAccounts | rg-data | eastus" in result
    assert "status=" not in result.splitlines()[1]


def test_vm_status_formats_power_states(az_present, monkeypatch):
    payload = [
        {"name": "web-vm", "resourceGroup": "rg-prod",
         "hardwareProfile": {"vmSize": "Standard_B2s"},
         "powerState": "VM running", "publicIps": "20.1.2.3"},
        {"name": "old-vm", "resourceGroup": "rg-prod",
         "hardwareProfile": {"vmSize": "Standard_B1s"},
         "powerState": "VM deallocated", "publicIps": ""},
    ]
    monkeypatch.setattr(azt.subprocess, "run", lambda *a, **k: fake_completed(payload))
    registry = build_default_registry(make_settings())
    result = registry.call("azure_vm_status", {})
    assert "web-vm | rg-prod | Standard_B2s | VM running | public IP: 20.1.2.3" in result
    assert "old-vm | rg-prod | Standard_B1s | VM deallocated" in result
    assert "public IP" not in result.splitlines()[1]


# ------------------------------------------------------------- error paths

def test_nonzero_exit_returns_error_string(az_present, monkeypatch):
    monkeypatch.setattr(
        azt.subprocess, "run",
        lambda *a, **k: fake_completed("", returncode=1, stderr="Please run 'az login'"),
    )
    registry = build_default_registry(make_settings())
    result = registry.call("azure_list_resources", {})
    assert result.startswith("Error:")
    assert "az login" in result


def test_timeout_returns_error_string(az_present, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["az"], timeout=60)

    monkeypatch.setattr(azt.subprocess, "run", boom)
    registry = build_default_registry(make_settings())
    assert registry.call("azure_account", {}).startswith("Error:")


def test_write_tool_invokes_correct_az_command(az_present, monkeypatch):
    calls = []

    def spy(cmd, **kwargs):
        calls.append(cmd)
        return fake_completed("")

    monkeypatch.setattr(azt.subprocess, "run", spy)
    registry = build_default_registry(make_settings(allow_write=True))
    result = registry.call("azure_vm_stop", {"name": "web-vm", "resource_group": "rg-prod"})
    assert "deallocate" in result
    assert any("deallocate" in c for c in calls[0])
    assert "web-vm" in calls[0] and "rg-prod" in calls[0]


def test_entra_users_filter(az_present, monkeypatch):
    payload = [
        {"displayName": "Alice Smith", "userPrincipalName": "alice@x.com", "id": "1"},
        {"displayName": "Bob Jones", "userPrincipalName": "bob@x.com", "id": "2"},
    ]
    monkeypatch.setattr(azt.subprocess, "run", lambda *a, **k: fake_completed(payload))
    registry = build_default_registry(make_settings())
    result = registry.call("azure_entra_users", {"filter": "ali"})
    assert "Alice Smith | alice@x.com | 1" in result
    assert "Bob Jones" not in result
