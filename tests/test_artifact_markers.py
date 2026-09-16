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
