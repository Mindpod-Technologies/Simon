"""Artifact markers created by tools must always reach the final reply."""
from __future__ import annotations

from simon.agent import Agent
from simon.config import Settings


class MarkerLLM:
    """Calls a chart tool once, then paraphrases WITHOUT quoting the
    [artifact:...] marker from the tool output — the real-world behaviour
    this guard exists for."""

    model = "fake"
    model_fast = None
    last_route_reason = "test"

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "c1",
                    "name": "create_chart",
                    "arguments": {"title": "T"},
                }],
            }
        # Paraphrased answer with no marker quoted.
        return {"content": "Here is your chart, sir.", "tool_calls": []}


class MarkerRegistry:
    def schemas(self):
        return []

    def call(self, name, args):
        return ("Created chart 'T.png'.\n[artifact:charts/T.png]")

    def __contains__(self, name):
        return name == "create_chart"


def test_missing_artifact_marker_is_appended(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    agent = Agent(Settings(), registry=MarkerRegistry(),
                  session_id="test", llm=MarkerLLM())
    reply = agent.handle("make me a chart")
    assert "Here is your chart, sir." in reply
    assert "[artifact:charts/T.png]" in reply


def test_presentation_claim_without_tool_looks_dishonest(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    agent = Agent(Settings(), registry=MarkerRegistry(),
                  session_id="test", llm=MarkerLLM())
    assert agent._looks_dishonest(
        "Here is the pie chart showing the cloud focus, sir.")
    assert agent._looks_dishonest("Here's your graph, sir.")
    assert agent._looks_dishonest("The chart above summarises it.")
    # Legitimate replies must NOT trip the detector:
    assert not agent._looks_dishonest(
        "A pie chart would suit this data well, sir — shall I draw one?")


def test_claim_triggers_nudge_then_real_tool_call(tmp_path, monkeypatch):
    """Full loop: claim without call → nudge → model calls the tool →
    marker reaches the reply."""

    class ClaimThenCallLLM(MarkerLLM):
        def chat(self, messages, tools=None, model=None):
            self.calls += 1
            if self.calls == 1:
                return {"content": "Here is the pie chart, sir.",
                        "tool_calls": []}
            if self.calls == 2:  # first nudge → real tool call
                return {"content": "",
                        "tool_calls": [{"id": "c1", "name": "create_chart",
                                        "arguments": {"title": "T"}}]}
            return {"content": "Here is your chart, sir.", "tool_calls": []}

    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    agent = Agent(Settings(), registry=MarkerRegistry(),
                  session_id="test", llm=ClaimThenCallLLM())
    reply = agent.handle("make me a pie chart")
    assert "[artifact:charts/T.png]" in reply


def test_rendered_above_claim_is_dishonest(tmp_path, monkeypatch):
    """The exact K3 evasion seen in production: 'pie chart is rendered
    above' without calling create_chart."""
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    agent = Agent(Settings(), registry=MarkerRegistry(),
                  session_id="test", llm=MarkerLLM())
    assert agent._looks_dishonest(
        "Your **Team Split** pie chart is rendered above and available "
        "in the Artifacts panel — Engineering 60%, sir.")
    # Legitimate non-claims stay clean:
    assert not agent._looks_dishonest(
        "A pie chart would suit this data — shall I create one, sir?")
    assert not agent._looks_dishonest(
        "The chart would be rendered more clearly as a bar chart, sir.")
