"""Sub-agent runtime tests — no network, FakeLLM injected into children."""

import threading
import time

import pytest

from simon.config import Settings
from simon.subagents import SUBAGENT_TOOL_NAMES, SubAgentManager
from simon.tools import build_default_registry


class FakeLLM:
    """Immediately answers with a final report naming the task."""

    def chat(self, messages, tools=None):
        prompt = messages[-1]["content"]
        task_line = next((l for l in prompt.splitlines()
                          if l.startswith("Task: ")), "")
        return {"content": f"Report: completed. {task_line}",
                "tool_calls": []}


class FailingLLM:
    def chat(self, messages, tools=None):
        raise RuntimeError("boom")


class SlowLLM:
    """Blocks until released, to keep slots occupied."""

    def __init__(self, gate: threading.Event):
        self.gate = gate

    def chat(self, messages, tools=None):
        self.gate.wait(timeout=10)
        return {"content": "slow report", "tool_calls": []}


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    return Settings(simon_subagents_enabled=True,
                    simon_subagents_max_concurrent=3)


def wait_for(manager, task_id, want=("done", "failed", "cancelled"),
             timeout=5.0):
    """Poll until the task reaches one of ``want`` statuses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with manager._lock:
            status = manager._tasks[task_id].status
        if status in want:
            return status
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not finish in time")


def test_spawn_completes_and_result(settings):
    notes = []
    manager = SubAgentManager(settings, notify=notes.append,
                              llm_factory=lambda: FakeLLM())
    task_id = manager.spawn("research the best tea")
    assert isinstance(task_id, str) and len(task_id) == 8

    assert wait_for(manager, task_id) == "done"
    assert "done" in manager.status(task_id)
    result = manager.result(task_id)
    assert "Report: completed." in result
    assert "research the best tea" in result
    assert notes and notes[0].startswith(f"Sub-agent {task_id} done:")


def test_child_registry_excludes_subagent_tools(settings):
    registry = build_default_registry(settings, exclude=SUBAGENT_TOOL_NAMES)
    for name in SUBAGENT_TOOL_NAMES:
        assert name not in registry
    # Default registry (parent) does include them when enabled.
    parent = build_default_registry(settings)
    assert "spawn_agent" in parent
    # And exclusion is a no-op for other tools.
    assert len(registry) == len(parent) - len(SUBAGENT_TOOL_NAMES)


def test_cancel_queued_task(settings):
    gate = threading.Event()
    manager = SubAgentManager(settings, llm_factory=lambda: SlowLLM(gate))
    manager.max_concurrent = 1
    manager._slots = threading.BoundedSemaphore(1)
    try:
        first = manager.spawn("slow one")
        second = manager.spawn("queued one")
        wait_for(manager, first, want=("running",), timeout=5.0)
        assert manager.cancel(second) == f"Sub-agent {second} cancelled."
        assert "cancelled" in manager.status(second)
        assert "already cancelled" in manager.cancel(second)
    finally:
        gate.set()
    wait_for(manager, first)


def test_max_concurrent_respected(settings):
    gate = threading.Event()
    manager = SubAgentManager(settings, llm_factory=lambda: SlowLLM(gate))
    manager.max_concurrent = 3
    manager._slots = threading.BoundedSemaphore(3)
    try:
        ids = [manager.spawn(f"task {i}") for i in range(4)]
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with manager._lock:
                statuses = [manager._tasks[i].status for i in ids]
            if statuses.count("running") == 3 and "queued" in statuses:
                break
            time.sleep(0.02)
        else:
            raise AssertionError(f"unexpected statuses: {statuses}")
        assert statuses[3] == "queued"  # 4th task waits for a slot
    finally:
        gate.set()
    for task_id in ids:
        wait_for(manager, task_id)


def test_failing_child_captured(settings):
    manager = SubAgentManager(settings, llm_factory=lambda: FailingLLM())
    task_id = manager.spawn("doomed task")
    assert wait_for(manager, task_id) == "failed"
    assert "boom" in manager.result(task_id)
    assert "failed" in manager.status(task_id)


def test_manager_never_raises(settings):
    manager = SubAgentManager(settings, llm_factory=lambda: FakeLLM())
    assert "unknown sub-agent" in manager.status("nope")
    assert "unknown sub-agent" in manager.result("nope")
    assert "unknown sub-agent" in manager.cancel("nope")
    assert "No sub-agents" in manager.status()


def test_status_lists_all(settings):
    manager = SubAgentManager(settings, llm_factory=lambda: FakeLLM())
    ids = [manager.spawn(f"t{i}") for i in range(2)]
    for task_id in ids:
        wait_for(manager, task_id)
    listing = manager.status()
    for task_id in ids:
        assert task_id in listing
