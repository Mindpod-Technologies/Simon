"""MCP client: connect Simon to Model Context Protocol servers.

Simon reads a ``mcp.json`` file (Claude-Desktop-style format) listing local
MCP servers, spawns each one over stdio, discovers its tools, and registers
them on the tool registry as ``mcp_<server>_<tool>``. From the agent's point
of view they are ordinary tools.

Config file (default ``mcp.json`` in the working directory, override with
SIMON_MCP_CONFIG)::

    {
      "servers": {
        "filesystem": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
          "env": {"FOO": "bar"},          // optional
          "disabled": false                // optional
        }
      }
    }

Security note: MCP servers are local subprocesses running with Simon's
privileges, and their tools are auto-registered. Only configure servers you
trust — ``mcp.json`` is trusted configuration, exactly like ``.env``.

Architecture: a single process-wide :class:`MCPManager` owns a background
asyncio event loop (its own thread). Each server gets one long-lived task on
that loop holding the stdio subprocess and ClientSession open. Tool calls
from Simon's synchronous tool path are submitted with
``asyncio.run_coroutine_threadsafe`` and block with a timeout. Because the
manager is a singleton, ``build_default_registry`` being called per-agent
(web, scheduler, jobs) never spawns duplicate server processes.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Optional

from .tools.base import Tool

log = logging.getLogger(__name__)

_DEFAULT_CONFIG = "mcp.json"
_MAX_TOOL_NAME = 64

_MANAGER: Optional["MCPManager"] = None
_MANAGER_LOCK = threading.Lock()


def load_server_specs(path: Path) -> dict[str, dict]:
    """Read a mcp.json-style config file into {name: spec}.

    Missing file → empty dict (MCP simply stays off). Malformed JSON or a
    malformed structure raises ValueError so the caller can log it loudly —
    silent misconfiguration is worse than no MCP.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text())
    servers = raw.get("servers")
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: expected a top-level 'servers' object")
    specs = {}
    for name, spec in servers.items():
        if not isinstance(spec, dict) or not spec.get("command"):
            log.warning("mcp: skipping server %r — needs at least 'command'",
                        name)
            continue
        if spec.get("disabled"):
            log.info("mcp: server %r disabled in config", name)
            continue
        specs[str(name)] = spec
    return specs


def _sanitize(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", text)


class MCPManager:
    """Owns MCP server subprocesses and their tool sessions."""

    def __init__(self, call_timeout: int = 120, start_timeout: int = 30):
        self.call_timeout = call_timeout
        self.start_timeout = start_timeout
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True,
            name="simon-mcp-loop")
        self._thread.start()
        self._sessions: dict[str, Any] = {}      # server -> ClientSession
        self._tasks: dict[str, asyncio.Task] = {}
        self.tools: dict[str, list[dict]] = {}   # server -> discovered tools
        self._closed = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def _run_server(self, name: str, spec: dict,
                          ready: "concurrent.futures.Future[list]") -> None:
        """Lifespan of one server: spawn, handshake, discover, park."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import get_default_environment, stdio_client

        env = None
        if spec.get("env"):
            env = {**get_default_environment(),
                   **{k: str(v) for k, v in spec["env"].items()}}
        params = StdioServerParameters(
            command=spec["command"],
            args=[str(a) for a in spec.get("args", [])],
            env=env,
            cwd=spec.get("cwd") or None,
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    self._sessions[name] = session
                    if not ready.done():
                        ready.set_result([
                            {"name": t.name,
                             "description": t.description or "",
                             "schema": (t.input_schema
                                        or {"type": "object",
                                            "properties": {}})}
                            for t in result.tools
                        ])
                    log.info("mcp: server %r up with %d tools",
                             name, len(result.tools))
                    await asyncio.Event().wait()  # park until cancelled
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # ExceptionGroup (from anyio TaskGroups) hides the root cause in
            # sub-exceptions — unwrap for the log.
            root = exc
            while hasattr(root, "exceptions") and root.exceptions:
                root = root.exceptions[0]
            log.warning("mcp: server %r failed: %r (root: %r)",
                        name, exc, root)
            if not ready.done():
                ready.set_exception(exc)

    def start_server(self, name: str, spec: dict) -> list[dict]:
        """Spawn one MCP server and return its discovered tool definitions.

        Raises on handshake failure — the caller logs and skips the server.
        """
        ready: "concurrent.futures.Future[list]" = \
            concurrent.futures.Future()

        def _schedule() -> None:
            self._tasks[name] = self._loop.create_task(
                self._run_server(name, spec, ready))

        self._loop.call_soon_threadsafe(_schedule)
        tools = ready.result(timeout=self.start_timeout)
        self.tools[name] = tools
        return tools

    def call_tool(self, server: str, tool_name: str,
                  arguments: dict) -> str:
        """Invoke a tool on a running server; blocks up to call_timeout."""
        session = self._sessions.get(server)
        if session is None:
            return f"Error: MCP server '{server}' is not running"
        try:
            result = asyncio.run_coroutine_threadsafe(
                session.call_tool(tool_name, arguments or {}),
                self._loop).result(timeout=self.call_timeout)
        except Exception as exc:
            return f"Error: MCP tool {server}/{tool_name} failed: {exc}"
        parts = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            parts.append(text if text is not None else str(block))
        out = "\n".join(p for p in parts if p)
        if getattr(result, "isError", False):
            return f"Error: {out or 'MCP tool reported an error'}"
        return out or "(MCP tool returned no content)"

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in self._tasks.values():
            self._loop.call_soon_threadsafe(task.cancel)
        if self._tasks:
            done = threading.Event()

            async def _wait_all():
                await asyncio.gather(*self._tasks.values(),
                                     return_exceptions=True)
                done.set()

            asyncio.run_coroutine_threadsafe(_wait_all(), self._loop)
            done.wait(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        log.info("mcp: manager shut down")


# ---------------------------------------------------------------------- #
# Singleton + registry wiring
# ---------------------------------------------------------------------- #

def get_manager(settings) -> Optional[MCPManager]:
    """Return the process-wide MCP manager, creating and connecting it on
    first use. Returns None when MCP is disabled or no servers are
    configured. Never raises — MCP must never break startup."""
    global _MANAGER
    if not getattr(settings, "simon_mcp_enabled", True):
        return None
    with _MANAGER_LOCK:
        if _MANAGER is not None:
            return _MANAGER
        config_path = Path(getattr(settings, "simon_mcp_config", "")
                           or _DEFAULT_CONFIG)
        try:
            specs = load_server_specs(config_path)
        except Exception as exc:
            log.warning("mcp: cannot load %s: %s", config_path, exc)
            return None
        if not specs:
            return None
        manager = MCPManager(
            call_timeout=getattr(settings, "simon_mcp_call_timeout", 120),
            start_timeout=getattr(settings, "simon_mcp_start_timeout", 30))
        started = False
        for name, spec in specs.items():
            try:
                manager.start_server(name, spec)
                started = True
            except Exception as exc:
                log.warning("mcp: skipping server %r: %s", name, exc)
        if not started:
            manager.shutdown()
            return None
        atexit.register(manager.shutdown)
        _MANAGER = manager
        return manager


def reset_manager_for_tests() -> None:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is not None:
            _MANAGER.shutdown()
            _MANAGER = None


def register_mcp_tools(registry, settings) -> int:
    """Connect to configured MCP servers (once per process) and register
    their tools on ``registry`` as ``mcp_<server>_<tool>``.

    Returns the number of tools registered. Never raises.
    """
    try:
        manager = get_manager(settings)
    except Exception as exc:  # pragma: no cover - belt and braces
        log.warning("mcp: manager unavailable: %s", exc)
        return 0
    if manager is None:
        return 0
    count = 0
    for server, tools in manager.tools.items():
        for t in tools:
            wrapper_name = _sanitize(
                f"mcp_{server}_{t['name']}")[:_MAX_TOOL_NAME]
            description = (t["description"] or "MCP tool")
            if server:
                description = f"[MCP:{server}] {description}"
            registry.register(Tool(
                name=wrapper_name,
                description=description[:1024],
                parameters=t["schema"],
                func=_make_call(manager, server, t["name"]),
            ))
            count += 1
    if count:
        log.info("mcp: registered %d tools from %d server(s)",
                 count, len(manager.tools))
    return count


def _make_call(manager: MCPManager, server: str, tool_name: str):
    def _call(**kwargs) -> str:
        return manager.call_tool(server, tool_name, kwargs)
    return _call
