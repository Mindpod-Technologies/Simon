"""Sub-agent runtime: Simon can spawn child agents to work in parallel.

Each sub-agent is a fresh :class:`simon.agent.Agent` running in a background
thread with its own session and a tool registry that *excludes* the
sub-agent tools themselves, so children can never spawn grandchildren
(depth cap of 1). Results are captured on :class:`SubAgentTask` records and
optionally pushed out via a ``notify`` callback (Telegram, scheduler, ...).

The manager never raises to its caller: every error is captured into the
task record with status ``failed``.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: Tool names children must not receive (prevents grandchildren — depth cap 1).
SUBAGENT_TOOL_NAMES = {
    "spawn_agent", "list_agents", "agent_status", "agent_result", "cancel_agent",
}

VALID_STATUSES = {"queued", "running", "done", "failed", "cancelled"}


@dataclass
class SubAgentTask:
    """Record of one spawned sub-agent."""

    id: str
    task: str
    status: str = "queued"
    result: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def elapsed(self) -> float:
        """Seconds since creation (until completion if finished)."""
        return (self.finished_at or time.time()) - self.created_at


class SubAgentManager:
    """Spawns and tracks background sub-agents.

    ``notify`` (optional) is called with a short human-readable string when a
    task completes, so interfaces like Telegram can push results. At most
    ``max_concurrent`` sub-agents run at once; extras stay ``queued`` until a
    slot frees up. Thread-safe: all task-dict access is lock-guarded.
    """

    def __init__(self, settings: Any,
                 notify: Optional[Callable[[str], None]] = None,
                 llm_factory: Optional[Callable[[], Any]] = None) -> None:
        """Create a manager.

        ``llm_factory`` is used by tests to inject a fake LLM into child
        agents; in production it is ``None`` and each child builds a real
        :class:`simon.llm.LLM` from settings.
        """
        self.settings = settings
        self.notify = notify
        self.llm_factory = llm_factory
        self.max_concurrent: int = int(
            getattr(settings, "simon_subagents_max_concurrent", 3) or 3)
        self._tasks: dict[str, SubAgentTask] = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(self.max_concurrent)

    # ------------------------------------------------------------- public

    def spawn(self, task: str, context: str = "") -> str:
        """Spawn a sub-agent for ``task``; returns the new task id."""
        try:
            task_id = uuid.uuid4().hex[:8]
            record = SubAgentTask(id=task_id, task=task)
            with self._lock:
                self._tasks[task_id] = record
            thread = threading.Thread(
                target=self._run, args=(record, context),
                name=f"simon-subagent-{task_id}", daemon=True)
            thread.start()
            logger.info("spawned sub-agent %s: %.80s", task_id, task)
            return task_id
        except Exception as exc:  # noqa: BLE001 - never raise to caller
            logger.exception("failed to spawn sub-agent")
            return f"Error: could not spawn sub-agent: {exc}"

    def status(self, task_id: Optional[str] = None) -> str:
        """Human-readable status for one task, or a list of all tasks."""
        with self._lock:
            if task_id:
                record = self._tasks.get(task_id)
                if record is None:
                    return f"Error: unknown sub-agent '{task_id}'"
                return self._format(record)
            records = sorted(self._tasks.values(), key=lambda r: r.created_at)
        if not records:
            return "No sub-agents have been spawned."
        return "\n".join(self._format(r) for r in records)

    def result(self, task_id: str) -> str:
        """Result text for a task, or a note that it is still running."""
        with self._lock:
            record = self._tasks.get(task_id)
        if record is None:
            return f"Error: unknown sub-agent '{task_id}'"
        if record.status in ("queued", "running"):
            return f"Sub-agent {task_id} is still {record.status}."
        return record.result or f"Sub-agent {task_id} finished with no output."

    def cancel(self, task_id: str) -> str:
        """Cancel a queued or running task (cooperative for running tasks)."""
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return f"Error: unknown sub-agent '{task_id}'"
            if record.status in ("done", "failed", "cancelled"):
                return f"Sub-agent {task_id} already {record.status}."
            record.status = "cancelled"
            record.result = "Cancelled."
            record.finished_at = time.time()
        logger.info("cancelled sub-agent %s", task_id)
        return f"Sub-agent {task_id} cancelled."

    # ------------------------------------------------------------ internal

    def _run(self, record: SubAgentTask, context: str) -> None:
        """Worker body: wait for a slot, run the child agent, capture output."""
        with self._slots:
            if record.status == "cancelled":
                return
            record.status = "running"
            try:
                from .agent import Agent
                from .tools import build_default_registry

                registry = build_default_registry(
                    self.settings, exclude=SUBAGENT_TOOL_NAMES)
                llm = self.llm_factory() if self.llm_factory else None
                child = Agent(self.settings, registry=registry,
                              session_id=f"sub-{record.id}", llm=llm)
                prompt = (
                    "You are a sub-agent of Simon. Complete this task and "
                    "return a concise final report.\n"
                    f"Task: {record.task}\n"
                    f"Context: {context or '(none)'}"
                )
                output = child.handle(prompt)
                if record.status == "cancelled":
                    return
                record.result = output
                record.status = "done"
            except Exception as exc:  # noqa: BLE001 - capture, never raise
                logger.exception("sub-agent %s failed", record.id)
                if record.status != "cancelled":
                    record.result = f"Error: sub-agent failed: {exc}"
                    record.status = "failed"
            finally:
                record.finished_at = record.finished_at or time.time()
                if record.status == "done" and self.notify:
                    try:
                        self.notify(
                            f"Sub-agent {record.id} done: {record.result[:200]}")
                    except Exception:  # noqa: BLE001 - notify must not kill us
                        logger.exception("notify callback failed for %s",
                                         record.id)

    @staticmethod
    def _format(record: SubAgentTask) -> str:
        return (f"{record.id}: {record.status} "
                f"({record.elapsed():.1f}s) — {record.task[:60]}")
