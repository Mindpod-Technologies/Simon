"""Slack tools: registry wiring, channel resolution, approval gating."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from simon import approvals
from simon.tools import ToolRegistry
from simon.tools.slack_tool import register_slack_tools


class _FakeClient:
    """Minimal slack_sdk WebClient stand-in."""

    def conversations_list(self, limit=200, cursor=None):
        return {"channels": [
            {"id": "C1", "name": "general", "num_members": 5},
            {"id": "C2", "name": "random", "num_members": 3}]}

    def conversations_history(self, channel, limit):
        return {"messages": [{"ts": "1.0", "user": "U1", "text": "hello"}]}

    def chat_postMessage(self, channel, text):
        return {"ts": "9.9"}

    def reactions_add(self, channel, timestamp, name):
        return {"ok": True}


def _registry():
    settings = SimpleNamespace(slack_bot_token="xoxb-test")
    registry = ToolRegistry()
    with patch("slack_sdk.WebClient", return_value=_FakeClient()):
        register_slack_tools(registry, settings)
    return registry


def test_registers_when_token_present():
    registry = _registry()
    for name in ("slack_list_channels", "slack_read_channel",
                 "slack_search", "slack_post", "slack_react"):
        assert name in registry


def test_no_token_no_tools():
    registry = ToolRegistry()
    register_slack_tools(registry, SimpleNamespace(slack_bot_token=""))
    assert "slack_post" not in registry


def test_list_and_read_by_channel_name():
    registry = _registry()
    with patch("slack_sdk.WebClient", return_value=_FakeClient()):
        out = registry.call("slack_list_channels", {})
        assert "#general (C1)" in out
        out = registry.call("slack_read_channel",
                            {"channel": "#general", "count": 5})
        assert "hello" in out


def test_unknown_channel_is_an_error():
    registry = _registry()
    with patch("slack_sdk.WebClient", return_value=_FakeClient()):
        out = registry.call("slack_read_channel", {"channel": "#nope"})
        assert out.startswith("Error")


def test_slack_post_is_approval_gated():
    summary = approvals.assess("slack_post",
                               {"channel": "#general", "text": "hi team"})
    assert summary and "general" in summary
    assert approvals.assess("slack_read_channel", {"channel": "#x"}) is None
    assert approvals.assess("slack_react",
                            {"channel": "#x", "timestamp": "1",
                             "emoji": "tada"}) is None
    assert approvals.assess("slack_pin",
                            {"channel": "#x", "timestamp": "1"})
