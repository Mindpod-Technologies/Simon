"""Tests for Simon's tool system. No network access required."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from simon.tools import Tool, ToolRegistry, build_default_registry, tool


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    """Idempotency records live in the memory DB — isolate per test so a
    prior test's mutating call never dedupes this test's first call."""
    from simon import memory
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH", str(tmp_path / "t.db"))
    memory.init_db()


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


def test_call_drops_model_invented_arguments():
    """Local models invent params not in the schema (e.g. web_search called
    with topn=5 crashed live turns on 2026-09-26). Invented args must be
    dropped, not crash the tool."""
    registry = ToolRegistry()
    seen = {}

    def _search(query):
        seen["query"] = query
        return "results"

    registry.register(Tool(
        name="web_search",
        description="Search the web.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        func=_search,
    ))
    out = registry.call("web_search", {"query": "bento app", "topn": 5})
    assert out == "results"
    assert seen == {"query": "bento app"}


def test_call_passes_everything_to_kwargs_tools():
    """Tools whose func takes **kwargs receive all args unfiltered."""
    registry = ToolRegistry()
    registry.register(Tool(
        name="flex",
        description="Flexible.",
        parameters={"type": "object", "properties": {}},
        func=lambda **kw: ",".join(sorted(kw)),
    ))
    assert registry.call("flex", {"a": 1, "b": 2}) == "a,b"


def test_invented_arg_filter_cached_and_recomputed_on_reregister():
    registry = ToolRegistry()
    registry.register(Tool(
        name="t", description="x", parameters={}, func=lambda a: f"v1:{a}"))
    assert registry.call("t", {"a": 1, "junk": 2}) == "v1:1"
    registry.register(Tool(
        name="t", description="x", parameters={},
        func=lambda a, junk: f"v2:{a}{junk}"))
    assert registry.call("t", {"a": 1, "junk": 2}) == "v2:12"


def test_missing_required_arg_returns_schema_guidance():
    """Wrong-args TypeError must come back as model-actionable guidance
    naming the required fields, not opaque Python-speak."""
    registry = ToolRegistry()
    registry.register(Tool(
        name="create_document",
        description="Create a document.",
        parameters={"type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"]},
        func=lambda title: f"doc:{title}",
    ))
    out = registry.call("create_document", {"file_path": "x.md"})
    assert out.startswith("Error")
    assert "title" in out  # names the accepted argument(s) for self-correction
    # And the corrected retry then works:
    assert registry.call("create_document", {"title": "PRD"}) == "doc:PRD"


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


def test_file_tools_extra_allowed_dirs(tmp_path):
    """SIMON_ALLOWED_DIRS grants read+write beyond the workspace — the
    'access my Mac's files' switch — while everything else stays refused."""
    workspace = tmp_path / "ws"
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.txt").write_text("hello from docs")
    settings = make_settings(str(workspace))
    settings.simon_allowed_dirs = str(docs)
    reg = build_default_registry(settings)
    # read/list/write inside the extra dir all work (absolute paths)
    assert reg.call("read_file", {"path": str(docs / "note.txt")}) == "hello from docs"
    assert reg.call("write_file", {"path": str(docs / "new.txt"),
                                   "content": "x"}).startswith("Wrote")
    assert (docs / "new.txt").read_text() == "x"
    assert "note.txt" in reg.call("list_files", {"path": str(docs)})
    # anything outside workspace + allowed dirs is still refused
    assert reg.call("read_file", {"path": "/etc/passwd"}).startswith("Error:")
    assert reg.call("write_file", {"path": str(tmp_path / "evil.txt"),
                                   "content": "x"}).startswith("Error:")


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


def test_mutating_tool_refuses_unknown_args():
    """Strict-arg contract: a mutating call with model-invented args is
    REFUSED with self-correction info — never silently executed with a
    different meaning (the 2026 'worst possible outcome' anti-pattern)."""
    registry = ToolRegistry()
    executed = []
    registry.register(Tool(
        name="send_email",
        description="Send mail.",
        parameters={"type": "object",
                    "properties": {"to": {"type": "string"}},
                    "required": ["to"]},
        func=lambda to: executed.append(to) or f"sent to {to}",
    ))
    out = registry.call("send_email", {"to": "a@b.com", "priority": "high"})
    assert out.startswith("Error") and "refused" in out
    assert "priority" in out and "to" in out
    assert executed == []  # nothing executed


def test_readonly_tool_still_drops_unknown_args():
    """Read-only tools keep the forgiving path — a stray arg changes
    nothing observable."""
    registry = ToolRegistry()
    registry.register(Tool(
        name="web_search", description="Search.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        func=lambda query: f"results for {query}",
    ))
    assert registry.call("web_search", {"query": "x", "topn": 5}) == \
        "results for x"


def test_mcp_mutation_names_classified_mutating():
    from simon.tools import _is_mutating
    from simon.tools.base import Tool as _T
    assert _is_mutating(_T(name="mcp_github_merge_pull_request",
                           description="", parameters={}, func=lambda: ""))
    assert not _is_mutating(_T(name="mcp_github_get_file_contents",
                               description="", parameters={}, func=lambda: ""))


def test_mutating_call_is_idempotent(tmp_path, monkeypatch):
    """An identical mutating call inside the TTL returns the recorded
    result instead of executing twice — no double-sent emails."""
    from simon import memory
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH", str(tmp_path / "s.db"))
    memory.init_db()
    registry = ToolRegistry()
    executed = []
    registry.register(Tool(
        name="send_email", description="Send mail.",
        parameters={"type": "object", "properties": {"to": {"type": "string"}}},
        func=lambda to: executed.append(to) or f"sent to {to}",
    ))
    args = {"to": "a@b.com"}
    first = registry.call("send_email", args)
    second = registry.call("send_email", args)
    assert executed == ["a@b.com"]          # executed exactly once
    assert first.startswith("sent to")
    assert "not repeated" in second


def test_different_args_execute_fresh(tmp_path, monkeypatch):
    from simon import memory
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH", str(tmp_path / "s.db"))
    memory.init_db()
    registry = ToolRegistry()
    executed = []
    registry.register(Tool(
        name="send_email", description="Send mail.",
        parameters={"type": "object", "properties": {"to": {"type": "string"}}},
        func=lambda to: executed.append(to) or f"sent to {to}",
    ))
    registry.call("send_email", {"to": "a@b.com"})
    registry.call("send_email", {"to": "c@d.com"})
    assert executed == ["a@b.com", "c@d.com"]


def test_error_results_are_never_cached(tmp_path, monkeypatch):
    from simon import memory
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH", str(tmp_path / "s.db"))
    memory.init_db()
    registry = ToolRegistry()
    calls = []
    registry.register(Tool(
        name="send_email", description="Send mail.",
        parameters={"type": "object", "properties": {"to": {"type": "string"}}},
        func=lambda to: calls.append(to) or "Error: smtp down",
    ))
    registry.call("send_email", {"to": "a@b.com"})
    registry.call("send_email", {"to": "a@b.com"})
    assert len(calls) == 2  # errors retry for real
