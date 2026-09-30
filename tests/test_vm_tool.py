"""VM desktop tools: registration gating and behavior (Docker mocked)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from simon.tools import ToolRegistry
from simon.tools import vm_tool


def _settings(**kw):
    base = {"simon_vm_enabled": True}
    base.update(kw)
    return SimpleNamespace(**base)


def test_no_tools_when_disabled():
    registry = ToolRegistry()
    vm_tool.register_vm_tools(registry, _settings(simon_vm_enabled=False))
    assert "vm_exec" not in registry


def test_tools_register_eagerly_availability_is_lazy():
    """Tools register unconditionally (enabled); each CALL checks the
    desktop's state — Docker is never probed at registry-build time."""
    registry = ToolRegistry()
    vm_tool.register_vm_tools(registry, _settings())
    for name in ("vm_status", "vm_exec", "vm_open_url", "vm_screenshot"):
        assert name in registry


def test_status_reports_watch_url():
    with patch.object(vm_tool, "_docker_ok", return_value=True), \
         patch.object(vm_tool, "_desktop_running", return_value=True):
        out = vm_tool._vm_status()
    assert "localhost:3000" in out


def test_exec_refuses_when_stopped():
    with patch.object(vm_tool, "_desktop_running", return_value=False):
        out = vm_tool._vm_exec("ls")
    assert out.startswith("Error")


def test_open_url_adds_scheme():
    calls = []
    with patch.object(vm_tool, "_desktop_running", return_value=True), \
         patch.object(vm_tool, "_exec",
                      lambda cmd, timeout=60: calls.append(cmd) or "ok"):
        out = vm_tool._vm_open_url("example.com")
    assert "xdg-open 'https://example.com'" in calls[0]
    assert "localhost:3000" in out


def test_exec_runs_inside_container():
    with patch.object(vm_tool, "_desktop_running", return_value=True), \
         patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(stdout="files\n",
                                               stderr="")
            out = vm_tool._vm_exec("ls")
    assert out == "files"
    argv = run.call_args[0][0]
    assert argv[:3] == ["docker", "exec", "simon-desktop"]
