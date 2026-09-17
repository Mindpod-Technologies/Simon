"""Plugin bookkeeping + the /api/plugins endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from simon import auth, memory, settings_api
from simon.tools import LOADED_PLUGINS, ToolRegistry, load_plugins
from simon.interfaces.web import create_app


def test_load_plugins_records_contributed_tools(tmp_path):
    plugin = tmp_path / "hello_plugin.py"
    plugin.write_text(
        "from simon.tools import tool\n"
        "\n"
        "@tool(name='say_hello', description='greet',\n"
        "      parameters={'type': 'object', 'properties': {}})\n"
        "def _hello() -> str:\n"
        "    return 'hello'\n"
        "\n"
        "def register(registry):\n"
        "    registry.register(_hello)\n"
    )
    (tmp_path / "broken_plugin.py").write_text("raise RuntimeError('boom')\n")
    LOADED_PLUGINS.clear()
    registry = ToolRegistry()
    load_plugins(registry, plugins_dir=tmp_path)
    assert LOADED_PLUGINS == {"hello_plugin.py": ["say_hello"]}
    assert registry.call("say_hello", {}) == "hello"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    memory.init_db()
    stored = auth.hash_password("test-owner-pass")
    monkeypatch.setattr(
        settings_api, "read_env",
        lambda path=None: ({"SIMON_OWNER_PASSWORD_HASH": stored}, []))

    class S:
        simon_workspace_dir = str(tmp_path / "workspace")
        simon_web_default_session = ""
        simon_allow_shell = False
        simon_mcp_config = str(tmp_path / "mcp.json")

        def canonical_session(self, interface, supplied):
            return supplied

    (tmp_path / "mcp.json").write_text(
        '{"mcpServers": {"github": {"command": "x"}, "fs": {"command": "y"}}}')
    app = create_app(S())
    c = TestClient(app)
    c.cookies.set(auth.COOKIE_NAME, auth.make_session(stored))
    return c


def test_plugins_endpoint_lists_plugins_and_mcp(client):
    r = client.get("/api/plugins")
    assert r.status_code == 200
    body = r.json()
    assert "plugins" in body and "mcp_servers" in body
    assert body["mcp_servers"] == ["fs", "github"]   # names only, no secrets


def test_plugins_endpoint_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    stored = auth.hash_password("test-owner-pass")
    monkeypatch.setattr(
        settings_api, "read_env",
        lambda path=None: ({"SIMON_OWNER_PASSWORD_HASH": stored}, []))

    class S:
        simon_workspace_dir = str(tmp_path / "workspace")
        simon_web_default_session = ""
        simon_allow_shell = False
        simon_mcp_config = ""

        def canonical_session(self, interface, supplied):
            return supplied

    assert TestClient(create_app(S())).get("/api/plugins").status_code == 401
