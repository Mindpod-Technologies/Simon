"""Core 2.0 M2: durable assignment state — a commitment lives outside
trimmable history, survives context budgeting, and closes only on real
receipts or the owner's explicit cancellation."""

import pytest

from simon import assignments, memory
from simon.agent import Agent
from simon.config import Settings

ASSIGNMENT = ("I want you to do research on developing an application that "
              "competes with Bento, then come up with the system design and "
              "PRD, then email me at jarasf@mindpodtech.com.")

MULTI_PLAN = {"choice": "tool_multi", "confidence": 0.9,
              "tools_required": True, "multi_step": True}
CHAT_PLAN = {"choice": "chat", "confidence": 0.9,
             "tools_required": False, "multi_step": False}


class GreetingLLM:
    """Ignores the assignment and greets — the M1 failure model."""

    model = "smart"
    model_fast = "fast"
    frontier_enabled = False

    def __init__(self, replies=None):
        self.replies = list(replies or [
            "Hello Jae! I'm here to help with any tasks you need."] * 3)
        self.calls = 0

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        text = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        return {"content": text, "tool_calls": []}


class RecoveringLLM(GreetingLLM):
    """Greets first, then ACTS when the gate demands receipts."""

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        if self.calls == 1:
            return {"content": "Hello Jae! How can I help today?",
                    "tool_calls": []}
        if self.calls == 2:
            return {"content": None, "tool_calls": [{
                "id": "c1", "name": "web_search",
                "arguments": {"query": "Bento task management app"}}]}
        return {"content": "Report ready: Bento organizes work by day.",
                "tool_calls": []}


class FakeRegistry:
    def __init__(self):
        self.executed = []

    def schemas(self):
        return [{"type": "function", "function": {
            "name": "web_search", "description": "search the web",
            "parameters": {"type": "object", "properties": {}}}}]

    def call(self, name, args):
        self.executed.append((name, args))
        return "Bento is a daily task-planning app with a focus view."


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    memory.init_db()
    assignments.init_db()
    return tmp_path


def _agent(llm, registry, monkeypatch, plan, session="m2-test"):
    monkeypatch.setattr("simon.decisions.plan_turn", lambda text: plan)
    return Agent(Settings(), registry=registry, session_id=session, llm=llm)


# ---- storage lifecycle -------------------------------------------------

def test_open_active_close_lifecycle(db):
    aid = assignments.open_assignment("s1", "research Bento")
    current = assignments.active("s1")
    assert current["id"] == aid
    assert current["goal"] == "research Bento"
    assignments.record_receipts(aid, ["web_search", "web_search"])
    assignments.record_receipts(aid, ["create_document"])
    assert assignments.active("s1")["receipts"] == [
        "web_search", "web_search", "create_document"]
    assignments.close(aid, assignments.DONE)
    assert assignments.active("s1") is None


def test_new_assignment_supersedes_open_one(db):
    first = assignments.open_assignment("s1", "old task")
    second = assignments.open_assignment("s1", "new task")
    current = assignments.active("s1")
    assert current["id"] == second and current["goal"] == "new task"
    assert first != second


def test_sessions_are_isolated(db):
    assignments.open_assignment("s1", "task for s1")
    assert assignments.active("s2") is None


# ---- prompt injection survives trimming --------------------------------

def test_assignment_injected_despite_history_trim(db, monkeypatch):
    registry = FakeRegistry()
    agent = _agent(GreetingLLM(), registry, monkeypatch, CHAT_PLAN,
                   session="trim-test")
    assignments.open_assignment("trim-test", ASSIGNMENT)
    # Stuff history far beyond the 6000-token budget so trimming MUST fire.
    for i in range(30):
        memory.add_message("trim-test", "user", f"old message {i} " + "x" * 2000)
        memory.add_message("trim-test", "assistant", "old reply " + "y" * 2000)
    messages = agent._build_messages("how is my task coming along?")
    system = messages[0]["content"]
    assert "ACTIVE ASSIGNMENT" in system
    assert "competes with Bento" in system
    # Trimming really happened: the oldest stuffed messages are gone.
    assert not any("old message 0 " in str(m.get("content"))
                   for m in messages[1:])


def test_no_assignment_no_injection(db, monkeypatch):
    registry = FakeRegistry()
    agent = _agent(GreetingLLM(), registry, monkeypatch, CHAT_PLAN,
                   session="clean-test")
    messages = agent._build_messages("hello")
    assert "ACTIVE ASSIGNMENT" not in messages[0]["content"]


# ---- agent integration --------------------------------------------------

def test_multi_step_plan_opens_assignment(db, monkeypatch):
    registry = FakeRegistry()
    agent = _agent(GreetingLLM(), registry, monkeypatch, MULTI_PLAN,
                   session="open-test")
    agent.handle(ASSIGNMENT)
    current = assignments.active("open-test")
    assert current is not None
    assert "competes with Bento" in current["goal"]


def test_gate_failure_keeps_assignment_open(db, monkeypatch):
    """The honest 'nothing has been done' outcome must NOT close the task —
    the commitment survives so the next turn resumes it."""
    registry = FakeRegistry()
    agent = _agent(GreetingLLM(), registry, monkeypatch, MULTI_PLAN,
                   session="stuck-test")
    reply = agent.handle(ASSIGNMENT)
    assert "nothing has been done" in reply.lower()
    assert assignments.active("stuck-test") is not None


def test_receipts_close_assignment_done(db, monkeypatch):
    registry = FakeRegistry()
    agent = _agent(RecoveringLLM(), registry, monkeypatch, MULTI_PLAN,
                   session="done-test")
    reply = agent.handle(ASSIGNMENT)
    assert "Bento organizes work by day" in reply
    assert assignments.active("done-test") is None  # settled as done


def test_owner_cancellation_closes_assignment(db, monkeypatch):
    registry = FakeRegistry()
    agent = _agent(GreetingLLM(), registry, monkeypatch, MULTI_PLAN,
                   session="cancel-test")
    agent.handle(ASSIGNMENT)
    assert assignments.active("cancel-test") is not None
    reply = agent.handle("never mind, forget it")
    assert "cancelled" in reply.lower()
    assert assignments.active("cancel-test") is None


def test_cancellation_words_without_assignment_pass_through(db, monkeypatch):
    """Negative control: 'never mind' with nothing open is a normal turn."""
    registry = FakeRegistry()
    agent = _agent(GreetingLLM(), registry, monkeypatch, CHAT_PLAN,
                   session="plain-test")
    reply = agent.handle("never mind")
    assert reply.startswith("Hello Jae")
    assert assignments.active("plain-test") is None
