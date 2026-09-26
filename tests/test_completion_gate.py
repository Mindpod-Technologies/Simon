"""Core 2.0 completion gate: tool-mandatory turns may not answer without
receipts. Replays the real Telegram failure classes from 2026-09-25."""

import pytest

from simon import memory
from simon.agent import Agent
from simon.config import Settings

ASSIGNMENT = ("I want you to do research on developing an application that "
              "competes with Bento, then come up with the system design and "
              "PRD, then email me at jarasf@mindpodtech.com.")


class GreetingLLM:
    """The failure-1 model: ignores the assignment, greets — twice."""

    model = "smart"
    model_fast = "fast"
    frontier_enabled = False

    def __init__(self, replies=None):
        self.replies = list(replies or [
            "Hello Jae! I'm here to help with any tasks you need.",
            "Hello again, sir — what would you like to tackle today?"])
        self.calls = 0

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        text = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        return {"content": text, "tool_calls": []}


class JsonFabricatorLLM(GreetingLLM):
    """The failure-3 model: emits a hallucinated action as raw JSON text."""

    def __init__(self):
        super().__init__([
            '{"org":"Mindpod-Technologies","name":"ai-automation",'
            '"private":true,"auto_init":false}'] * 2)


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
    return tmp_path


def _agent(llm, registry, monkeypatch, plan):
    monkeypatch.setattr("simon.decisions.plan_turn", lambda text: plan)
    return Agent(Settings(), registry=registry, session_id="gate-test",
                 llm=llm)


MULTI_PLAN = {"choice": "tool_multi", "confidence": 0.9,
              "tools_required": True, "multi_step": True}
CHAT_PLAN = {"choice": "chat", "confidence": 0.9,
             "tools_required": False, "multi_step": False}


def test_greeting_answer_is_rejected_on_mandatory_turn(db, monkeypatch):
    registry = FakeRegistry()
    agent = _agent(GreetingLLM(), registry, monkeypatch, MULTI_PLAN)
    reply = agent.handle(ASSIGNMENT)
    assert "Hello Jae" not in reply
    assert "nothing has been done" in reply.lower()
    assert registry.executed == []  # and no fake action was executed


def test_json_fabrication_is_rejected_too(db, monkeypatch):
    registry = FakeRegistry()
    agent = _agent(JsonFabricatorLLM(), registry, monkeypatch, MULTI_PLAN)
    reply = agent.handle(ASSIGNMENT)
    assert '"org"' not in reply and "ai-automation" not in reply
    assert "nothing has been done" in reply.lower()


def test_gate_retry_with_receipts_delivers(db, monkeypatch):
    registry = FakeRegistry()
    agent = _agent(RecoveringLLM(), registry, monkeypatch, MULTI_PLAN)
    reply = agent.handle(ASSIGNMENT)
    assert registry.executed == [("web_search",
                                  {"query": "Bento task management app"})]
    assert "Bento organizes work by day" in reply


def test_chat_turn_unaffected_by_gate(db, monkeypatch):
    """Negative control: a genuine greeting must sail through untouched."""
    registry = FakeRegistry()
    agent = _agent(GreetingLLM(), registry, monkeypatch, CHAT_PLAN)
    reply = agent.handle("Good evening Simon")
    assert reply == "Hello Jae! I'm here to help with any tasks you need."


def test_planner_unavailable_keeps_current_path(db, monkeypatch):
    """plan_turn → None (Jev down / backend off): today's behavior stands."""
    registry = FakeRegistry()
    agent = _agent(GreetingLLM(), registry, monkeypatch, plan=None)
    reply = agent.handle("Good evening Simon")
    assert reply.startswith("Hello Jae")


def test_low_confidence_plan_does_not_gate(db, monkeypatch):
    registry = FakeRegistry()
    agent = _agent(
        GreetingLLM(), registry, monkeypatch,
        {"choice": "tool_multi", "confidence": 0.3,
         "tools_required": True, "multi_step": True})
    reply = agent.handle("Good evening Simon")
    assert reply.startswith("Hello Jae")
