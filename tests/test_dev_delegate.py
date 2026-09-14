"""Tests for simon.tools.dev_delegate — the external coding-CLI bridge."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest import mock

from simon.tools import ToolRegistry
from simon.tools import dev_delegate


def _settings(**kw):
    base = dict(simon_workspace_dir="./workspace",
                simon_dev_delegate_enabled=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_tool_not_registered_when_disabled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workspace").mkdir()
    reg = ToolRegistry()
    dev_delegate.register_dev_delegate_tools(reg, _settings())
    assert "delegate_dev" not in reg.names()


def test_tool_registered_when_enabled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workspace").mkdir()
    reg = ToolRegistry()
    dev_delegate.register_dev_delegate_tools(
        reg, _settings(simon_dev_delegate_enabled=True))
    assert "delegate_dev" in reg.names()


def test_unknown_engine_rejected(tmp_path):
    s = _settings(simon_workspace_dir=str(tmp_path))
    out = dev_delegate.delegate_dev(s, "do a thing", engine="gpt-5")
    assert "unknown engine" in out


def test_path_traversal_rejected(tmp_path):
    (tmp_path / "ws").mkdir()
    s = _settings(simon_workspace_dir=str(tmp_path / "ws"))
    out = dev_delegate.delegate_dev(s, "do a thing", path="../../etc")
    assert "escapes the workspace" in out


def test_missing_directory_rejected(tmp_path):
    (tmp_path / "ws").mkdir()
    s = _settings(simon_workspace_dir=str(tmp_path / "ws"))
    out = dev_delegate.delegate_dev(s, "do a thing", path="nope")
    assert "does not exist" in out


def test_empty_task_rejected(tmp_path):
    s = _settings(simon_workspace_dir=str(tmp_path))
    assert "empty task" in dev_delegate.delegate_dev(s, "   ")


def test_command_shape_and_env_scrub(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    s = _settings(simon_workspace_dir=str(ws))
    captured = {}

    class FakeProc:
        returncode = 0
        stdout = "done the thing"
        stderr = ""

    def fake_run(cmd, cwd, env, capture_output, text, timeout):
        captured.update(cmd=cmd, cwd=cwd, env=env, timeout=timeout)
        return FakeProc()

    with mock.patch.object(subprocess, "run", fake_run):
        out = dev_delegate.delegate_dev(s, "fix the bug", engine="claude")
    assert captured["cmd"][0].endswith("claude")
    assert captured["cmd"][1] == "-p"
    assert "fix the bug" in captured["cmd"]
    assert captured["cwd"] == str(ws)
    assert captured["timeout"] == dev_delegate._TIMEOUT_S
    # no Simon/LLM secrets leak into the child env
    assert not any(k.startswith(("SIMON", "LLM_", "OPENAI_API", "ANTHROPIC_API"))
                   for k in captured["env"])
    assert captured["env"].get("CI") == "true"
    assert "done the thing" in out
    assert "ok" in out


def test_codex_command_shape(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    s = _settings(simon_workspace_dir=str(ws))
    captured = {}

    class FakeProc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return FakeProc()

    with mock.patch.object(subprocess, "run", fake_run):
        dev_delegate.delegate_dev(s, "add tests", engine="codex")
    assert captured["cmd"][0].endswith("codex")
    assert captured["cmd"][1:] == ["exec", "add tests"]


def test_cursor_command_shape(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    s = _settings(simon_workspace_dir=str(ws))
    captured = {}

    class FakeProc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return FakeProc()

    with mock.patch.object(subprocess, "run", fake_run):
        dev_delegate.delegate_dev(s, "refactor module", engine="cursor")
    assert captured["cmd"][0].endswith("cursor-agent")
    assert captured["cmd"][1:] == ["-p", "refactor module",
                                   "--output-format", "text"]


def test_resolve_bin_uses_path_first():
    with mock.patch.object(dev_delegate.shutil, "which",
                           return_value="/opt/homebrew/bin/claude"):
        assert dev_delegate._resolve_bin("claude") == "/opt/homebrew/bin/claude"


def test_resolve_bin_falls_back_to_known_locations():
    with mock.patch.object(dev_delegate.shutil, "which", return_value=None), \
         mock.patch.object(dev_delegate.os.path, "exists",
                           side_effect=lambda p: p == "/opt/homebrew/bin/codex"):
        assert dev_delegate._resolve_bin("codex") == "/opt/homebrew/bin/codex"


def test_resolve_bin_returns_bare_name_when_unfound():
    with mock.patch.object(dev_delegate.shutil, "which", return_value=None), \
         mock.patch.object(dev_delegate.os.path, "exists", return_value=False):
        assert dev_delegate._resolve_bin("codex") == "codex"


def test_timeout_reported(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    s = _settings(simon_workspace_dir=str(ws))
    with mock.patch.object(subprocess, "run",
                           side_effect=subprocess.TimeoutExpired("claude", 5)):
        out = dev_delegate.delegate_dev(s, "big task")
    assert "exceeded" in out and "killed" in out


def test_missing_cli_reported(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    s = _settings(simon_workspace_dir=str(ws))
    with mock.patch.object(subprocess, "run",
                           side_effect=FileNotFoundError()):
        out = dev_delegate.delegate_dev(s, "task")
    assert "not found" in out


def test_long_output_truncated(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    s = _settings(simon_workspace_dir=str(ws))

    class FakeProc:
        returncode = 0
        stdout = "x" * 9000
        stderr = ""

    with mock.patch.object(subprocess, "run", lambda *a, **k: FakeProc()):
        out = dev_delegate.delegate_dev(s, "task")
    assert "truncated" in out
    assert len(out) < 9000
