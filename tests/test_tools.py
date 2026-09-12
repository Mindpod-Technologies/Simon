"""Tests for Simon's tool system. No network access required."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from simon.tools import Tool, ToolRegistry, build_default_registry, tool


def make_settings(workspace: str, allow_shell: bool = False) -> SimpleNamespace:
    """Minimal Settings-compatible stub (matches simon.config.Settings fields)."""
    return SimpleNamespace(
        simon_workspace_dir=str(workspace),
        simon_allow_shell=allow_shell,
        imap_host="",
        smtp_host="",
        google_calendar_ics="",
        homeassistant_url="",
        homeassistant_token="",
    )


# ------------------------------------------------------------- registry

def test_register_and_call_roundtrip():
    registry = ToolRegistry()
    registry.register(Tool(
        name="echo",
        description="Echo back the text.",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        func=lambda text: f"echo: {text}",
    ))
    assert registry.call("echo", {"text": "hello"}) == "echo: hello"
    schemas = registry.schemas()
    assert schemas == [{
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echo back the text.",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        },
    }]


def test_unknown_tool_returns_error_string_not_exception():
    registry = ToolRegistry()
    result = registry.call("does_not_exist", {})
    assert isinstance(result, str)
    assert result.startswith("Error:")
    assert "does_not_exist" in result


def test_call_never_raises_on_tool_exception():
    registry = ToolRegistry()

    def boom():
        raise RuntimeError("kaput")

    registry.register(Tool("boom", "always fails", {"type": "object", "properties": {}}, boom))
    result = registry.call("boom", {})
    assert result.startswith("Error:")
    assert "kaput" in result


def test_tool_decorator():
    @tool("shout", "Shout the text.", {"type": "object", "properties": {"text": {"type": "string"}}})
    def shout(text: str) -> str:
        return text.upper()

    assert isinstance(shout, Tool)
    registry = ToolRegistry()
    registry.register(shout)
    assert registry.call("shout", {"text": "hi"}) == "HI"


# ------------------------------------------------------------- calculator

@pytest.fixture()
def registry(tmp_path):
    return build_default_registry(make_settings(tmp_path))


@pytest.mark.parametrize("expr,expected", [
    ("2 + 3 * 4", "14"),
    ("(2 + 3) * 4", "20"),
    ("10 / 4", "2.5"),
    ("2 ** 10", "1024"),
    ("-7 + 2", "-5"),
    ("17 % 5", "2"),
])
def test_calculator_math(registry, expr, expected):
    assert registry.call("calculator", {"expression": expr}) == expected


@pytest.mark.parametrize("expr", [
    "__import__('os').system('id')",
    "open('/etc/passwd').read()",
    "x + 1",
    "eval('1+1')",
    "1; import os",
])
def test_calculator_rejects_unsafe_input(registry, expr):
    result = registry.call("calculator", {"expression": expr})
    assert result.startswith("Error:"), f"unsafe expression was evaluated: {expr!r}"


# ------------------------------------------------------------------ notes

def test_notes_roundtrip(tmp_path):
    reg = build_default_registry(make_settings(tmp_path))
    assert reg.call("read_notes", {}) == "(no notes yet)"
    assert reg.call("take_note", {"note": "buy milk"}) == "Noted."
    reg.call("take_note", {"note": "call mum"})
    notes = reg.call("read_notes", {})
    assert "buy milk" in notes
    assert "call mum" in notes
    assert (tmp_path / "notes.txt").exists()


# ------------------------------------------------------------------ files

def test_file_tools_roundtrip_and_confinement(tmp_path):
    reg = build_default_registry(make_settings(tmp_path))
    assert reg.call("write_file", {"path": "a/b.txt", "content": "hi"}).startswith("Wrote")
    assert reg.call("read_file", {"path": "a/b.txt"}) == "hi"
    listing = reg.call("list_files", {"path": "."})
    assert "a/" in listing
    # path escapes are rejected as error strings, never raise
    assert reg.call("read_file", {"path": "../outside.txt"}).startswith("Error:")
    assert reg.call("write_file", {"path": "/etc/evil", "content": "x"}).startswith("Error:")


def test_shell_not_registered_by_default(tmp_path):
    reg = build_default_registry(make_settings(tmp_path, allow_shell=False))
    assert "run_shell" not in reg
    assert reg.call("run_shell", {"command": "echo hi"}).startswith("Error:")


def test_shell_registered_when_enabled(tmp_path):
    reg = build_default_registry(make_settings(tmp_path, allow_shell=True))
    result = reg.call("run_shell", {"command": "echo hello-simon"})
    assert "hello-simon" in result
    assert "exit code 0" in result


# ---------------------------------------------------------------- plugins

def test_example_plugin_joke_loaded(tmp_path):
    reg = build_default_registry(make_settings(tmp_path))
    assert "joke" in reg
    result = reg.call("joke", {})
    assert isinstance(result, str) and result
