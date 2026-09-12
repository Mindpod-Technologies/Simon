"""MCP client tests: config loading plus a full round-trip against a real
fake MCP server (a tiny stdio server script run in a subprocess)."""

import json
import sys
import textwrap

import pytest

from simon import mcp_client
from simon.mcp_client import MCPManager, load_server_specs
from simon.tools import ToolRegistry

FAKE_SERVER = textwrap.dedent("""
    from mcp.server.mcpserver import MCPServer

    m = MCPServer("fake")

    @m.tool()
    def echo(text: str) -> str:
        \"\"\"Echo text back with a prefix.\"\"\"
        return "echo:" + text

    @m.tool()
    def boom() -> str:
        \"\"\"Always fails.\"\"\"
        raise RuntimeError("kaboom")

    m.run(transport="stdio")
""")


@pytest.fixture()
def fake_server_script(tmp_path):
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER)
    return script


@pytest.fixture()
def manager():
    m = MCPManager(call_timeout=30, start_timeout=30)
    yield m
    m.shutdown()


# --------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------- #

def test_load_server_specs_missing_file(tmp_path):
    assert load_server_specs(tmp_path / "nope.json") == {}


def test_load_server_specs_malformed_raises(tmp_path):
    bad = tmp_path / "mcp.json"
    bad.write_text('{"servers": [1, 2]}')
    with pytest.raises(ValueError):
        load_server_specs(bad)


def test_load_server_specs_skips_disabled_and_commandless(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"servers": {
        "good": {"command": "python3", "args": ["s.py"]},
        "off": {"command": "python3", "disabled": True},
        "broken": {"args": ["no-command"]},
    }}))
    specs = load_server_specs(cfg)
    assert list(specs) == ["good"]
    assert specs["good"]["args"] == ["s.py"]


# --------------------------------------------------------------------- #
# Round-trip against a real stdio MCP server
# --------------------------------------------------------------------- #

def test_round_trip_discover_and_call(manager, fake_server_script):
    tools = manager.start_server("fake", {
        "command": sys.executable,
        "args": [str(fake_server_script)],
    })
    names = {t["name"] for t in tools}
    assert names == {"echo", "boom"}

    out = manager.call_tool("fake", "echo", {"text": "tea"})
    assert out == "echo:tea"

    err = manager.call_tool("fake", "boom", {})
    assert "Error" in err  # server-side failures surface as error text


def test_call_unknown_server(manager):
    assert "not running" in manager.call_tool("ghost", "echo", {})


def test_start_server_bad_command_raises(manager):
    with pytest.raises(Exception):
        manager.start_server("bad", {"command": "/nonexistent/binary-xyz"})


def test_registry_registration(manager, fake_server_script):
    manager.start_server("fake", {
        "command": sys.executable,
        "args": [str(fake_server_script)],
    })
    manager.tools["odd-name"] = [{
        "name": "weird tool!",
        "description": "",
        "schema": {"type": "object", "properties": {}},
    }]

    registry = ToolRegistry()

    class S:  # minimal settings stub
        simon_mcp_enabled = True

    # Register by hand (register_mcp_tools uses the singleton).
    from simon.tools.base import Tool
    count = 0
    for server, tools in manager.tools.items():
        for t in tools:
            wrapper = mcp_client._sanitize(f"mcp_{server}_{t['name']}")
            registry.register(Tool(
                name=wrapper,
                description=t["description"] or "MCP tool",
                parameters=t["schema"],
                func=mcp_client._make_call(manager, server, t["name"]),
            ))
            count += 1

    assert count == 3
    registered = {s["function"]["name"] for s in registry.schemas()}
    assert "mcp_fake_echo" in registered
    assert "mcp_odd_name_weird_tool_" in registered  # sanitized
    # The wrapper actually invokes the remote tool.
    assert registry.call("mcp_fake_echo", {"text": "hi"}) == "echo:hi"


def test_get_manager_disabled():
    class S:
        simon_mcp_enabled = False
    assert mcp_client.get_manager(S()) is None
