"""Sandbox tool: registration gating + isolated-run command construction."""

import subprocess

from simon.config import Settings
from simon.tools import ToolRegistry, sandbox_tool


def test_not_registered_without_docker(monkeypatch):
    monkeypatch.setattr(sandbox_tool, "docker_available", lambda: False)
    registry = ToolRegistry()
    sandbox_tool.register_sandbox_tool(registry, Settings())
    assert "sandbox_exec" not in registry


def test_not_registered_when_flag_off(monkeypatch):
    monkeypatch.setattr(sandbox_tool, "docker_available", lambda: True)
    registry = ToolRegistry()
    sandbox_tool.register_sandbox_tool(
        registry, Settings(simon_sandbox_enabled=False))
    assert "sandbox_exec" not in registry


def test_registered_with_docker_and_isolation_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_tool, "docker_available", lambda: True)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout="hello sandbox\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = ToolRegistry()
    sandbox_tool.register_sandbox_tool(
        registry, Settings(simon_workspace_dir=str(tmp_path)))
    out = registry.call("sandbox_exec", {"code": "print('hi')"})
    assert out == "hello sandbox"
    cmd = captured["cmd"]
    joined = " ".join(cmd)
    assert "--network none" in joined
    assert "--memory 512m" in joined and "--cpus 1" in joined
    assert "--read-only" in joined
    assert f"{tmp_path}:/work" in joined
    assert cmd[0] == "docker" and cmd[1] == "run"


def test_nonzero_exit_reports_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_tool, "docker_available", lambda: True)

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 3, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = ToolRegistry()
    sandbox_tool.register_sandbox_tool(
        registry, Settings(simon_workspace_dir=str(tmp_path)))
    out = registry.call("sandbox_exec", {"code": "import sys; sys.exit(3)"})
    assert "Error (exit 3)" in out and "boom" in out


def test_timeout_reports_kill(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_tool, "docker_available", lambda: True)

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = ToolRegistry()
    sandbox_tool.register_sandbox_tool(
        registry, Settings(simon_workspace_dir=str(tmp_path)))
    out = registry.call("sandbox_exec", {"code": "while True: pass"})
    assert "exceeded" in out and "killed" in out


def test_empty_code_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_tool, "docker_available", lambda: True)
    registry = ToolRegistry()
    sandbox_tool.register_sandbox_tool(
        registry, Settings(simon_workspace_dir=str(tmp_path)))
    assert "needs code" in registry.call("sandbox_exec", {"code": "  "})
