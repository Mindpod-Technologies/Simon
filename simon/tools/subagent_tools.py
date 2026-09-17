"""Sub-agent tools: let Simon spawn and manage child agents in parallel.

A single module-level :class:`simon.subagents.SubAgentManager` singleton is
created lazily so every tool call (and every session) shares one pool.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from .base import Tool

log = logging.getLogger(__name__)

_manager: Optional[Any] = None
_manager_lock = threading.Lock()


def get_manager(settings: Any = None, notify=None) -> Any:
    """Return the shared SubAgentManager, creating it on first use."""
    global _manager
    with _manager_lock:
        if _manager is None:
            from ..subagents import SubAgentManager

            if notify is None and settings is not None:
                # Default delivery: push sub-agent completions to the owner's
                # Telegram so delegated work reports back on its own.
                try:
                    if getattr(settings, "telegram_bot_token", ""):
                        from ..interfaces.telegram_bot import make_notify
                        notify = make_notify(settings)
                except Exception:  # noqa: BLE001 - never block creation
                    log.exception("sub-agent notify setup failed")
            _manager = SubAgentManager(settings, notify=notify)
            log.info("sub-agent manager initialised")
        return _manager


def reset_manager() -> None:
    """Drop the singleton (used by tests)."""
    global _manager
    with _manager_lock:
        _manager = None


def register_subagent_tools(registry, settings) -> None:
    """Register spawn/list/status/result/cancel sub-agent tools."""
    manager = get_manager(settings)

    def spawn_agent(task: str, context: str = "") -> str:
        task_id = manager.spawn(task, context)
        return f"Spawned sub-agent {task_id} for: {task[:80]}"

    def list_agents() -> str:
        return manager.status()

    def agent_status(task_id: str = "") -> str:
        return manager.status(task_id or None)

    def agent_result(task_id: str) -> str:
        return manager.result(task_id)

    def cancel_agent(task_id: str) -> str:
        return manager.cancel(task_id)

    registry.register(Tool(
        name="spawn_agent",
        description=(
            "Spawn a background sub-agent to work on a task in parallel. Use "
            "this for long or multi-step research, or to run several pieces of "
            "work at once instead of sequentially. Always give the sub-agent "
            "fully self-contained instructions — it does NOT see this "
            "conversation, so include everything it needs in 'task' (and "
            "optional extra background in 'context'). Returns a task id; "
            "check its output later with agent_result. Each sub-agent burns "
            "LLM tokens, so only spawn when parallelism is genuinely useful."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string",
                         "description": "Self-contained task instructions."},
                "context": {"type": "string",
                            "description": "Optional background context."},
            },
            "required": ["task"],
        },
        func=spawn_agent,
    ))
    registry.register(Tool(
        name="list_agents",
        description="List all spawned sub-agents with status and elapsed time.",
        parameters={"type": "object", "properties": {}},
        func=list_agents,
    ))
    registry.register(Tool(
        name="agent_status",
        description=(
            "Check the status of one sub-agent by task id, or of all "
            "sub-agents when task_id is omitted."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string",
                            "description": "Optional sub-agent task id."},
            },
        },
        func=agent_status,
    ))
    registry.register(Tool(
        name="agent_result",
        description=(
            "Get a sub-agent's final report by task id. Returns a note if it "
            "is still running — in that case wait and check again."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string",
                            "description": "Sub-agent task id."},
            },
            "required": ["task_id"],
        },
        func=agent_result,
    ))
    registry.register(Tool(
        name="cancel_agent",
        description="Cancel a queued or running sub-agent by task id.",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string",
                            "description": "Sub-agent task id."},
            },
            "required": ["task_id"],
        },
        func=cancel_agent,
    ))
