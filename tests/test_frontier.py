"""Frontier-tier (cloud escalation model) tests — no network access."""

from types import SimpleNamespace

import pytest

from simon.agent import Agent
from simon.config import Settings
from simon.llm import LLM


# ---------------------------------------------------------------------------
# LLM-class tests with a mocked OpenAI client factory
# ---------------------------------------------------------------------------

class _FakeCompletions:
    def __init__(self, client):
        self._client = client

    def create(self, **kwargs):
        self._client.requests.append(kwargs)
        message = SimpleNamespace(content=self._client.reply, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeOpenAI:
    """Records every client instance and the requests each one receives."""

    instances: list = []

    def __init__(self, base_url=None, api_key=None):
        self.base_url = base_url
        self.api_key = api_key
        self.reply = "ok"
        self.requests: list = []
        self.chat = SimpleNamespace(completions=_FakeCompletions(self))
        FakeOpenAI.instances.append(self)


@pytest.fixture()
def fake_openai(monkeypatch):
    FakeOpenAI.instances = []
    monkeypatch.setattr("simon.llm.OpenAI", FakeOpenAI)
    return FakeOpenAI


def _settings(**overrides):
    base = {"llm_base_url": "http://local-llm/v1", "llm_api_key": "",
            "llm_model": "smart-model", "llm_model_fast": "fast-model",
            "llm_router_enabled": True, "llm_frontier_api_key": ""}
    base.update(overrides)
    return Settings(**base)


def test_wants_frontier_phrases():
    # Unbound call — pure text heuristic, no client needed.
    assert LLM.wants_frontier(None, "please use kimi for this one")
    assert LLM.wants_frontier(None, "ask the frontier model")
    assert LLM.wants_frontier(None, "use your biggest brain")
    assert not LLM.wants_frontier(None, "what time is it")
    assert not LLM.wants_frontier(None, "")


def test_frontier_disabled_without_key(fake_openai):
    llm = LLM(_settings())
    assert not llm.frontier_enabled
    assert llm._frontier_client is None
    # Only one client (the main one) was constructed.
    assert len(fake_openai.instances) == 1
    # Even if asked for the frontier model name, calls stay on the main
    # client — the tier is off without a key.
    llm.chat([{"role": "user", "content": "hi"}], model=llm.model_frontier)
    assert len(fake_openai.instances[0].requests) == 1


def test_frontier_routes_to_frontier_client(fake_openai):
    llm = LLM(_settings(llm_frontier_api_key="sk-test",
                        llm_frontier_model="kimi-k3",
                        llm_frontier_reasoning_effort="high"))
    assert llm.frontier_enabled
    assert len(fake_openai.instances) == 2
    main, frontier = fake_openai.instances
    assert frontier.base_url == "https://api.moonshot.ai/v1"
    assert frontier.api_key == "sk-test"

    # Frontier-model call goes to the frontier client, with the reasoning
    # effort passed through and WITHOUT the Ollama sampling extras.
    out = llm.chat([{"role": "user", "content": "hi"}], model="kimi-k3")
    assert out == {"content": "ok", "tool_calls": []}
    assert len(frontier.requests) == 1 and not main.requests
    req = frontier.requests[0]
    assert req["model"] == "kimi-k3"
    assert req.get("extra_body") == {"reasoning_effort": "high"}

    # Default calls stay on the main client.
    llm.chat([{"role": "user", "content": "hi"}])
    assert len(main.requests) == 1
    assert len(frontier.requests) == 1


# ---------------------------------------------------------------------------
# Agent-level routing with a scripted fake LLM
# ---------------------------------------------------------------------------

class _DummyRegistry:
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
        return f"Error: unknown tool '{name}'"


class FrontierFakeLLM:
    model = "smart-model"
    model_fast = "fast-model"
    model_frontier = "kimi-k3"
    frontier_enabled = True
    last_route_reason = ""

    def __init__(self):
        self.seen_models: list = []

    def route_model(self, user_text, history_len, memory_hits):
        self.last_route_reason = "simple turn"
        return self.model_fast

    def wants_frontier(self, user_text):
        return "use kimi" in (user_text or "").lower()

    def chat(self, messages, tools=None, model=None):
        self.seen_models.append(model)
        return {"content": "Frontier says hello, sir.", "tool_calls": []}


class FailingFakeLLM(FrontierFakeLLM):
    """Every non-frontier call fails — the frontier must take over."""

    def chat(self, messages, tools=None, model=None):
        self.seen_models.append(model)
        if model != self.model_frontier:
            raise RuntimeError("local model down")
        return {"content": "Frontier to the rescue, sir.", "tool_calls": []}


class LoopingFakeLLM(FrontierFakeLLM):
    """Local tiers keep issuing (failing) tool calls until the loop
    exhausts; the frontier then answers directly."""

    def chat(self, messages, tools=None, model=None):
        self.seen_models.append(model)
        if model == self.model_frontier:
            return {"content": "Frontier untangled it, sir.",
                    "tool_calls": []}
        return {"content": None, "tool_calls": [{
            "id": f"call_{len(self.seen_models)}",
            "name": "dummy", "arguments": {}}]}


def _agent(tmp_path, monkeypatch, llm):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    return Agent(Settings(), registry=_DummyRegistry(),
                 session_id="frontier-test", llm=llm)


def test_explicit_frontier_request_routes_whole_turn(tmp_path, monkeypatch):
    llm = FrontierFakeLLM()
    agent = _agent(tmp_path, monkeypatch, llm)
    reply = agent.handle("this one is tricky — please use kimi")
    assert reply == "Frontier says hello, sir."
    assert llm.seen_models == ["kimi-k3"]
    assert llm.last_route_reason == "explicit frontier request"


def test_normal_turn_stays_local(tmp_path, monkeypatch):
    llm = FrontierFakeLLM()
    agent = _agent(tmp_path, monkeypatch, llm)
    agent.handle("hi there")
    assert llm.seen_models == ["fast-model"]


def test_named_tool_routes_to_smart_model(tmp_path, monkeypatch):
    """Tool-naming turns must not strand on the tool-less fast tier."""
    llm = FrontierFakeLLM()
    agent = _agent(tmp_path, monkeypatch, llm)
    agent.handle("use the dummy tool please")
    assert llm.seen_models == ["smart-model"]
    assert llm.last_route_reason == "named tool"


class _FileRegistry:
    def __contains__(self, name):
        return name == "list_files"

    def schemas(self):
        return [{"type": "function", "function": {
            "name": "list_files",
            "description": "List files in a directory.",
            "parameters": {"type": "object", "properties": {}}}}]

    def call(self, name, args):
        return "report.pdf\nnotes.txt"


class RefusingFakeLLM(FrontierFakeLLM):
    """Refuses the file request from caution, then complies when nudged."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def chat(self, messages, tools=None, model=None):
        self.seen_models.append(model)
        self.calls += 1
        if self.calls == 1:
            return {"content": ("I'm sorry, sir, but I don't have permission "
                                "to read files on your local machine."),
                    "tool_calls": []}
        if self.calls == 2:
            return {"content": None, "tool_calls": [{
                "id": "c1", "name": "list_files",
                "arguments": {"path": "/Users/x/Desktop"}}]}
        return {"content": "Your Desktop holds report.pdf and notes.txt, sir.",
                "tool_calls": []}


def test_false_refusal_is_nudged_into_tool_call(tmp_path, monkeypatch):
    """A refusal for a permitted file task must end in a real tool call and
    a grounded answer — not an apology."""
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    llm = RefusingFakeLLM()
    agent = Agent(Settings(), registry=_FileRegistry(),
                  session_id="refusal-test", llm=llm)
    reply = agent.handle("what files are on my desktop, please?")
    assert "report.pdf" in reply
    assert llm.calls >= 2


def test_explicit_tool_call_parser():
    from simon.agent import Agent
    assert Agent._explicit_tool_call(
        "use the list_files tool on /Users/x/Desktop and tell me "
        "what is there") == ("list_files", {"path": "/Users/x/Desktop"})
    assert Agent._explicit_tool_call(
        "please use the read_file tool with ~/notes.txt") == (
        "read_file", {"path": "~/notes.txt"})
    # write-capable tools are never auto-executed from phrasing alone
    assert Agent._explicit_tool_call(
        "use the write_file tool on /tmp/x") is None
    assert Agent._explicit_tool_call("what is on my desktop?") is None


def test_explicit_tool_request_executes_deterministically(tmp_path,
                                                          monkeypatch):
    """'use the X tool on Y' must run X directly — no model cooperation
    required — and the reply must come from the real output."""
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))

    class OneShotLLM(FrontierFakeLLM):
        def chat(self, messages, tools=None, model=None):
            self.seen_models.append(model)
            # The grounding note must carry the real tool output.
            grounded = any("report.pdf" in str(m.get("content", ""))
                           for m in messages)
            assert grounded, "model was not given the real tool output"
            return {"content": "Your Desktop holds report.pdf, sir.",
                    "tool_calls": []}

    llm = OneShotLLM()
    agent = Agent(Settings(), registry=_FileRegistry(),
                  session_id="explicit-test", llm=llm)
    reply = agent.handle("use the list_files tool on ~/Desktop please")
    assert "report.pdf" in reply
    assert len(llm.seen_models) == 1  # single grounded call, no loop


def test_local_failure_escalates_to_frontier(tmp_path, monkeypatch):
    llm = FailingFakeLLM()
    agent = _agent(tmp_path, monkeypatch, llm)
    reply = agent.handle("hi there")
    assert reply == "Frontier to the rescue, sir."
    assert llm.seen_models[0] == "fast-model"
    assert llm.seen_models[-1] == "kimi-k3"
    assert llm.last_route_reason == "local model failure"


def test_exhausted_loop_rescued_by_frontier(tmp_path, monkeypatch):
    llm = LoopingFakeLLM()
    agent = _agent(tmp_path, monkeypatch, llm)
    reply = agent.handle("hi there")
    assert reply == "Frontier untangled it, sir."
    assert llm.seen_models[-1] == "kimi-k3"
    assert llm.last_route_reason == "frontier rescue"
