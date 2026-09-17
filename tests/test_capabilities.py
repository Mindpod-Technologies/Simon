"""Capability questions get an instant, registry-built answer — no model.

Regression: the fast tier answered "tell me all of what you can do" with
"I cannot emit a tool call without a specific instruction" — hallucinated
protocol talk. The truth is knowable from the registry, for free.
"""

from __future__ import annotations

import pytest

from simon import capabilities, memory
from simon.agent import Agent
from simon.config import Settings


SCHEMAS = [
    {"function": {"name": "remember_fact", "description": "store"}},
    {"function": {"name": "recall_facts", "description": "recall"}},
    {"function": {"name": "fetch_url", "description": "fetch"}},
    {"function": {"name": "create_document", "description": "doc"}},
    {"function": {"name": "create_chart", "description": "chart"}},
    {"function": {"name": "read_recent_emails", "description": "mail"}},
    {"function": {"name": "send_email", "description": "mail"}},
    {"function": {"name": "schedule_task", "description": "sched"}},
    {"function": {"name": "spawn_subagent", "description": "sub"}},
    {"function": {"name": "load_skill", "description": "skill"}},
    {"function": {"name": "exotic_tool", "description": "x"}},
]


@pytest.mark.parametrize("text", [
    "What can you do?",
    "Hello Simon, tell me all all of what you can do",
    "what are your capabilities",
    "what do you do",
    "show me what you can do",
    # the exact phrasing that fell through to a confused model in the app:
    "explain some automations that you can do on my behalf?",
    "what automations can you run for me?",
    "which tasks could you handle",
    "how can you help me",
    "describe the things you can automate",
])
def test_capability_questions_match(text):
    assert capabilities.is_capability_question(text)


@pytest.mark.parametrize("text", [
    "can you do my taxes?",
    "can you do me a favour and check my email",
    "what can you do with this PDF I uploaded",   # a task, not a tour
    "tell me what you can do with this spreadsheet",
    "show me what you can do to fix this bug",
    "what time is it",
])
def test_non_capability_messages_pass_through(text):
    assert not capabilities.is_capability_question(text)


def test_answer_reflects_live_registry():
    reply = capabilities.capabilities_answer(SCHEMAS)
    assert "Remember" in reply
    assert "Charts" in reply
    assert "Email" in reply
    assert "exotic_tool" in reply          # ungrouped tools still surface
    assert "sir" in reply.lower()


def test_answer_handles_empty_registry():
    reply = capabilities.capabilities_answer([])
    assert "summary" in reply.lower()


class _Registry:
    def schemas(self):
        return SCHEMAS

    def execute(self, name, arguments):  # pragma: no cover - never called
        raise AssertionError("capability turns must not call tools")


class _CountingLLM:
    """Proves the intercept never touches a model."""

    model = "smart"
    model_fast = "fast"
    frontier_enabled = False

    def __init__(self):
        self.calls = 0

    def route_model(self, *a, **k):
        return self.model

    def wants_frontier(self, _text):
        return False

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        return {"content": "model answer", "tool_calls": []}


def test_agent_intercepts_capability_question_instantly(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH", str(tmp_path / "s.db"))
    llm = _CountingLLM()
    agent = Agent(Settings(), registry=_Registry(), session_id="cap",
                  interface="test", llm=llm)
    reply = agent.handle("Simon, what can you do?")
    assert llm.calls == 0                        # zero model latency
    assert "Remember" in reply and "sir" in reply.lower()


def test_agent_does_not_intercept_task_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH", str(tmp_path / "s.db"))
    llm = _CountingLLM()
    agent = Agent(Settings(), registry=_Registry(), session_id="cap2",
                  interface="test", llm=llm)
    reply = agent.handle("can you do the weekly report now")
    assert llm.calls >= 1                        # normal path taken
    assert reply == "model answer"


class _RouterLLM(_CountingLLM):
    """Routes everything to the fast model; records the model actually used."""

    def __init__(self):
        super().__init__()
        self.models_used = []

    def route_model(self, *a, **k):
        return self.model_fast

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        self.models_used.append(model)
        return {"content": "model answer", "tool_calls": []}


def _router_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH", str(tmp_path / "s.db"))
    llm = _RouterLLM()
    return Agent(Settings(), registry=_Registry(), session_id="rt",
                 interface="test", llm=llm), llm


def test_explicit_search_request_escalates_off_fast(tmp_path, monkeypatch):
    agent, llm = _router_agent(tmp_path, monkeypatch)
    agent.handle("Can you do a web search for that organizing app?")
    assert llm.models_used and llm.models_used[0] == "smart"


def test_look_it_up_escalates_off_fast(tmp_path, monkeypatch):
    agent, llm = _router_agent(tmp_path, monkeypatch)
    agent.handle("Look it up online for me please")
    assert llm.models_used[0] == "smart"


def test_plain_chat_stays_fast(tmp_path, monkeypatch):
    agent, llm = _router_agent(tmp_path, monkeypatch)
    agent.handle("What time is it roughly in Tokyo?")
    assert llm.models_used[0] == "fast"
