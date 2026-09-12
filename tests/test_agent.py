"""Agent loop test with a FakeLLM — no network access."""

import pytest

from simon.agent import Agent
from simon.config import Settings


class FakeLLM:
    """Issues one tool_call on the first turn, then a final answer."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "name": "dummy",
                    "arguments": {"value": "tea"},
                }],
            }
        # The tool result must have been appended to the conversation.
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert tool_msgs and tool_msgs[-1]["content"] == "dummy says tea"
        return {"content": "Your tea is served, sir.", "tool_calls": []}


class DummyRegistry:
    def __init__(self):
        self.executed = []

    def schemas(self):
        return [{
            "type": "function",
            "function": {
                "name": "dummy",
                "description": "A dummy tool.",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

    def call(self, name, args):
        self.executed.append((name, args))
        if name != "dummy":
            return f"Error: unknown tool '{name}'"
        return f"dummy says {args['value']}"


@pytest.fixture()
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    registry = DummyRegistry()
    return Agent(Settings(), registry=registry, session_id="test",
                 llm=FakeLLM())


def test_agent_runs_tool_then_replies(agent):
    reply = agent.handle("Fetch my tea, would you?")
    assert agent.registry.executed == [("dummy", {"value": "tea"})]
    assert reply == "Your tea is served, sir."


def test_agent_persists_history(agent, tmp_path, monkeypatch):
    from simon import memory

    agent.handle("Fetch my tea, would you?")
    history = memory.get_history("test")
    roles = [m["role"] for m in history]
    assert roles == ["user", "assistant"]
    assert history[-1]["content"] == "Your tea is served, sir."


def test_agent_system_prompt_injected(agent):
    from simon import memory
    from simon.persona import SIMON_SYSTEM_PROMPT

    memory.init_db()

    messages = agent._build_messages("hello")
    assert messages[0]["role"] == "system"
    assert "{date}" not in messages[0]["content"]
    assert "Simon" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "hello"}
    assert SIMON_SYSTEM_PROMPT.split("{date}")[0][:20] in messages[0]["content"]


class PseudoLLM:
    """Writes the tool call as TEXT instead of emitting a real tool_call,
    then answers normally once the system executes it."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": ('I shall schedule that for you.\n\n'
                            'dummy(value="zebra", hour=16)'),
                "tool_calls": [],
            }
        return {"content": "It is done, sir — for real this time.",
                "tool_calls": []}


class ContainsRegistry(DummyRegistry):
    def __contains__(self, name):
        return name == "dummy"

    def names(self):
        return ["dummy"]


def test_pseudo_tool_call_is_executed(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    registry = ContainsRegistry()
    agent = Agent(Settings(), registry=registry, session_id="pseudo",
                  llm=PseudoLLM())
    reply = agent.handle("Schedule the zebra tally, please.")
    # The text-written call must have been executed for real …
    assert registry.executed == [("dummy", {"value": "zebra", "hour": 16})]
    # … and the final reply must be the regenerated confirmation.
    assert reply == "It is done, sir — for real this time."


def test_extract_pseudo_call_ignores_unknown_and_nonliteral(tmp_path,
                                                            monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    agent = Agent(Settings(), registry=ContainsRegistry(),
                  session_id="pseudo2", llm=PseudoLLM())
    # Unknown function names are ignored.
    assert agent._extract_pseudo_call("call foobar(x=1) now") is None
    # Non-literal arguments are refused.
    assert agent._extract_pseudo_call("dummy(value=foo.bar)") is None


def test_looks_dishonest_patterns(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    agent = Agent(Settings(), registry=ContainsRegistry(),
                  session_id="dishonest", llm=PseudoLLM())
    # Adjective-style claim: "Recurring automation scheduled."
    assert agent._looks_dishonest("Recurring automation scheduled, sir.")
    # Fabricated result block.
    assert agent._looks_dishonest("Result from dummy_tool: all good")
    # Naming a registered tool without calling it.
    assert agent._looks_dishonest("I shall use dummy for that, sir.")
    # Plain honest answers pass.
    assert not agent._looks_dishonest(
        "The capital of France is Paris, sir.")


def test_fetch_claim_patterns(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    agent = Agent(Settings(), registry=ContainsRegistry(),
                  session_id="fetch", llm=PseudoLLM())
    assert agent._looks_dishonest(
        "Upon reviewing the page, I find that it describes Angelmind.")
    assert agent._looks_dishonest(
        "The website clearly states that Angelmind is a platform.")
    assert agent._looks_dishonest(
        "According to the link you provided, the product does X.")
    # Plain knowledge answers are fine.
    assert not agent._looks_dishonest(
        "Angelmind sounds like a product name, sir — tell me more?")


def test_router_detects_bare_urls():
    from simon.llm import classify_turn

    smart, reason = classify_turn(
        "Hi Simon, review our website www.mindpodtech.com and tell me "
        "what AngelMind is", 0, 0)
    assert smart and "URL" in reason

    smart, reason = classify_turn(
        "what is AngelMind from mindpodtech.com?", 0, 0)
    assert smart and "URL" in reason

    # Ordinary prose without a domain stays on the fast route.
    smart, _ = classify_turn("hi there", 0, 0)
    assert not smart


def test_reviewed_without_fetch_is_dishonest(tmp_path, monkeypatch):
    """Regression for the Angelmind incident: 'I have reviewed the website'
    with no tool call must trigger the guard."""
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    agent = Agent(Settings(), registry=ContainsRegistry(),
                  session_id="reviewed", llm=PseudoLLM())
    assert agent._looks_dishonest(
        "Certainly, sir. I have reviewed the website "
        "www.mindpodtech.com, and here is my summary.")
    assert agent._looks_dishonest(
        "I have fetched the page and it describes Angelmind.")


def test_present_continuous_claim_is_dishonest(tmp_path, monkeypatch):
    """'I am starting a background job' with no tool call must trigger."""
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    agent = Agent(Settings(), registry=ContainsRegistry(),
                  session_id="present", llm=PseudoLLM())
    assert agent._looks_dishonest(
        "I shall begin the task now, sir. I am starting a background job "
        "to research the history of the spork.")
    assert agent._looks_dishonest("I'm now queuing that for you, sir.")
    assert not agent._looks_dishonest("I am Simon, at your service, sir.")
