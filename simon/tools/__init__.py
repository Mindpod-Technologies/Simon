"""Simon's tool system: registry, decorator, and plugin loading."""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

from .base import Tool

log = logging.getLogger(__name__)

# Repo-root plugins/ directory (simon/tools/../.. == repo root, + plugins)
PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"


class ToolRegistry:
    """Holds Tool instances, exposes OpenAI-style schemas, dispatches calls.

    call() NEVER raises: unknown tools and tool exceptions are returned
    as "Error: ..." strings so the agent loop can carry on.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        """OpenAI tools-format schemas for all registered tools."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    def call(self, name: str, args: dict) -> str:
        t = self._tools.get(name)
        if t is None:
            return f"Error: unknown tool '{name}'"
        try:
            result = t.func(**(args or {}))
        except Exception as exc:  # noqa: BLE001 - must never raise
            log.warning("tool %r failed: %s", name, exc)
            return f"Error: {exc}"
        return result if isinstance(result, str) else str(result)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)

    def __len__(self) -> int:
        return len(self._tools)


def tool(name: str, description: str, parameters: dict):
    """Decorator turning a function into a Tool factory.

    Usage:
        @tool("echo", "Echo text back", {"type": "object", "properties": {...}})
        def echo(text: str) -> str: ...
    """

    def decorator(func):
        return Tool(name=name, description=description, parameters=parameters, func=func)

    return decorator


def load_plugins(registry: ToolRegistry, plugins_dir: Path | None = None) -> None:
    """Import each plugins/*.py module and call its register(registry) if present.

    Failures are logged and skipped so a bad plugin never breaks startup.
    """
    directory = plugins_dir or PLUGINS_DIR
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"simon_plugin_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load spec for {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            register = getattr(module, "register", None)
            if callable(register):
                register(registry)
                log.info("loaded plugin %s", path.name)
        except Exception as exc:  # noqa: BLE001 - bad plugin must not break startup
            log.warning("skipping plugin %s: %s", path.name, exc)
            sys.modules.pop(module_name, None)


def build_default_registry(settings, exclude: set[str] | None = None) -> ToolRegistry:
    """Builtin tools + optional integrations (if configured) + drop-in plugins.

    ``exclude`` (optional) is a set of tool names to strip from the final
    registry — used by the sub-agent runtime so child agents cannot spawn
    grandchildren (depth cap 1).
    """
    registry = ToolRegistry()

    from . import builtin

    builtin.register_builtin_tools(registry, settings)

    if getattr(settings, "imap_host", "") or getattr(settings, "smtp_host", ""):
        try:
            from . import email_tool

            email_tool.register_email_tools(registry, settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("email tools unavailable: %s", exc)

    if getattr(settings, "google_calendar_ics", ""):
        try:
            from . import calendar_tool

            calendar_tool.register_calendar_tools(registry, settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("calendar tools unavailable: %s", exc)

    if getattr(settings, "homeassistant_url", "") and getattr(settings, "homeassistant_token", ""):
        try:
            from . import homeassistant

            homeassistant.register_homeassistant_tools(registry, settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("homeassistant tools unavailable: %s", exc)

    if getattr(settings, "simon_azure_enabled", False):
        try:
            from . import azure_tool

            azure_tool.register_azure_tools(registry, settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("azure tools unavailable: %s", exc)

    if getattr(settings, "simon_subagents_enabled", True):
        try:
            from . import subagent_tools

            subagent_tools.register_subagent_tools(registry, settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("sub-agent tools unavailable: %s", exc)

    if getattr(settings, "simon_computer_use", False):
        try:
            from . import computer

            computer.register_computer_tools(registry, settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("computer-use tools unavailable: %s", exc)

    if getattr(settings, "simon_browser_enabled", True):
        try:
            from . import browser

            browser.register_browser_tools(registry, settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("browser tools unavailable: %s", exc)


    if getattr(settings, "simon_jobs_enabled", False):
        try:
            from . import jobs_tool

            jobs_tool.register_job_tools(registry, settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("job tools unavailable: %s", exc)

    try:
        from .. import docs

        docs.register_docs_tools(registry, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("document tools unavailable: %s", exc)

    try:
        from . import schedules_tool

        schedules_tool.register_schedule_tools(registry, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("schedule tools unavailable: %s", exc)

    try:
        from ..mcp_client import register_mcp_tools

        register_mcp_tools(registry, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("MCP tools unavailable: %s", exc)

    if getattr(settings, "simon_skills_enabled", True):
        try:
            from .. import skills as skills_mod

            registry.register(Tool(
                name="load_skill",
                description=(
                    "Load the full procedure for a named skill. Call this "
                    "FIRST when the user's request matches a skill listed in "
                    "the system prompt, then follow the returned procedure."),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "skill name from the index"},
                    },
                    "required": ["name"],
                },
                func=lambda name: skills_mod.load_body(name),
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("skills unavailable: %s", exc)

    load_plugins(registry)

    if exclude:
        for name in exclude:
            registry._tools.pop(name, None)
    return registry


__all__ = ["Tool", "ToolRegistry", "tool", "load_plugins", "build_default_registry"]
