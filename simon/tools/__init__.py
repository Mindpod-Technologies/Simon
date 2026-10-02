"""Simon's tool system: registry, decorator, and plugin loading."""
from __future__ import annotations

import importlib.util
import logging
import re
import sys
from pathlib import Path

from .base import Tool

log = logging.getLogger(__name__)

# Mutating tools get STRICT argument validation (unknown args = refusal,
# never a silent drop). Central classification: explicit flag on the Tool,
# the known builtin set, or a mutation-verb name pattern (covers MCP tools
# like mcp_github_merge_pull_request automatically).
_MUTATING_NAMES = {
    "send_email", "slack_post", "slack_pin", "write_file", "create_document",
    "schedule_task", "cancel_schedule", "start_job", "cancel_job",
    "remember_fact", "take_note", "ingest_note", "teach_skill",
    "delegate_dev", "ha_call_service", "run_shell",
}
_MUTATING_NAME_RE = re.compile(
    r"(push|merge|delete|remove|send|post|pay|charge|refund|transfer"
    r"|comment|invite|publish|deploy|destroy|terminate|create|write"
    r"|schedule|cancel|pin|book|order|purchase|execute)", re.IGNORECASE)


def _is_mutating(tool) -> bool:
    """True when the tool changes state or is externally visible."""
    return (bool(getattr(tool, "mutating", False))
            or tool.name in _MUTATING_NAMES
            or bool(_MUTATING_NAME_RE.search(tool.name)))

# Repo-root plugins/ directory (simon/tools/../.. == repo root, + plugins)
PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"


class ToolRegistry:
    """Holds Tool instances, exposes OpenAI-style schemas, dispatches calls.

    call() NEVER raises: unknown tools and tool exceptions are returned
    as "Error: ..." strings so the agent loop can carry on.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._accepted: dict[str, "set[str] | None"] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        self._accepted.pop(tool.name, None)

    def _accepted_args(self, tool: Tool) -> "set[str] | None":
        """Parameter names the tool's function accepts, or None when it
        takes **kwargs (everything passes through). Cached per tool."""
        if tool.name in self._accepted:
            return self._accepted[tool.name]
        import inspect
        try:
            params = inspect.signature(tool.func).parameters
            accepted = None if any(
                p.kind is inspect.Parameter.VAR_KEYWORD
                for p in params.values()) else set(params)
        except (TypeError, ValueError):  # builtins without signatures
            accepted = None
        self._accepted[tool.name] = accepted
        return accepted

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
        args = dict(args or {})
        # Robustness: local models routinely invent parameters ("topn",
        # "num_results") that are not in the schema. For READ-ONLY tools,
        # dropping them beats crashing — the call still produces real
        # receipts. For MUTATING tools an invented arg may carry meaning
        # (a recipient, a namespace, a dry_run flag) — executing without it
        # performs a DIFFERENT action than the model intended, with a real
        # receipt to show for it. Refuse instead, with self-correction info.
        accepted = self._accepted_args(t)
        if accepted is not None:
            unknown = [k for k in args if k not in accepted]
            if unknown:
                if _is_mutating(t):
                    log.warning("tool %r REFUSED: model-invented arg(s) %s "
                                "on a mutating call", name, unknown)
                    return (f"Error: tool '{name}' refused: unknown "
                            f"argument(s) {unknown}. This action changes "
                            f"state, so nothing was executed. Accepted "
                            f"arguments: {sorted(accepted)}. Re-issue with "
                            f"only those.")
                log.warning("tool %r: dropping model-invented arg(s) %s",
                            name, unknown)
                args = {k: v for k, v in args.items() if k in accepted}
        # Idempotency for mutating calls: an identical repeat within the TTL
        # (gate retry, nudge loop, refired turn) returns the recorded result
        # instead of executing twice — no double-sent emails, ever. Tools
        # that manage their own clobber protection (idempotent=True) opt out.
        if _is_mutating(t) and not getattr(t, "idempotent", False):
            from .. import idempotency
            key = idempotency.key_for(name, args)
            try:
                prior = idempotency.lookup(key)
            except Exception:  # pragma: no cover - never break a call
                prior = None
            if prior is not None:
                log.info("tool %r: identical call already ran — returning "
                         "recorded result instead of re-executing", name)
                return (prior + "\n(Already executed — identical call "
                        "completed moments ago; not repeated.)")
        try:
            result = t.func(**args)
        except TypeError as exc:
            # The model called with wrong/missing args — reply with the
            # schema's required fields in plain language so its next call
            # self-corrects, instead of opaque Python-speak.
            required = (t.parameters or {}).get("required") or []
            if required and "required positional argument" in str(exc):
                log.warning("tool %r arg mismatch: %s", name, exc)
                return (f"Error: tool '{name}' was called with wrong or "
                        f"missing arguments. Required argument(s): "
                        f"{', '.join(required)}. Retry the call with exactly "
                        f"these argument names.")
            log.warning("tool %r failed: %s", name, exc)
            return f"Error: {exc}"
        except Exception as exc:  # noqa: BLE001 - must never raise
            log.warning("tool %r failed: %s", name, exc)
            return f"Error: {exc}"
        result = result if isinstance(result, str) else str(result)
        if _is_mutating(t):
            from .. import idempotency
            try:
                idempotency.record(key, name, result)
            except Exception:  # pragma: no cover - never break a call
                pass
        return result

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


# Bookkeeping for the UI: which drop-in plugins loaded and what tools each
# contributed. Keyed by file name, filled during build_default_registry.
LOADED_PLUGINS: dict[str, list[str]] = {}


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
            before = set(registry._tools)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            register = getattr(module, "register", None)
            if callable(register):
                register(registry)
                LOADED_PLUGINS[path.name] = sorted(set(registry._tools) - before)
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

    # Microsoft Graph mail — M365 tenants where basic IMAP/SMTP auth is
    # disabled. Takes precedence (registers after) when GRAPH_* is set.
    try:
        from . import graph_mail

        graph_mail.register_graph_mail_tools(registry, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("graph mail tools unavailable: %s", exc)

    # Slack workspace tools — Simon acts on the workspace (read, search,
    # react), not just chats in it. Posting is approval-gated.
    try:
        from . import slack_tool

        slack_tool.register_slack_tools(registry, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("slack tools unavailable: %s", exc)

    # Simon's own desktop VM (Kasm/Webtop container) — registers only when
    # the container is actually running; see deploy/simon-desktop.yml.
    try:
        from . import vm_tool

        vm_tool.register_vm_tools(registry, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("vm tools unavailable: %s", exc)

    # Fleet/provisioning (Simon Cloud per-customer instances) — the
    # Agent-Zero pipeline. provision_customer is approval-gated.
    try:
        from . import fleet_tool

        fleet_tool.register_fleet_tools(registry, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("fleet tools unavailable: %s", exc)

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

    try:
        from . import dev_delegate

        dev_delegate.register_dev_delegate_tools(registry, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("dev-delegate tools unavailable: %s", exc)

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
        from . import sandbox_tool

        sandbox_tool.register_sandbox_tool(registry, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("sandbox tool unavailable: %s", exc)

    try:
        from . import learn_tool

        learn_tool.register_learn_tools(registry, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("learn tools unavailable: %s", exc)

    try:
        from .. import docs

        docs.register_docs_tools(registry, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("document tools unavailable: %s", exc)

    try:
        from .. import charts

        charts.register_chart_tools(registry, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("chart tools unavailable: %s", exc)

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
