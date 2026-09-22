"""Swarm-review regression guards (Claude/Codex findings, 2026-09-21)."""

import pytest

from simon import memory
from simon.agent import Agent
from simon.config import Settings
from simon.tools.builtin import _calculator, _fetch_url, _is_private_host


class _PlainLLM:
    model = "smart"
    model_fast = "fast"
    frontier_enabled = False

    def chat(self, messages, tools=None, model=None):
        return {"content": "from the model, sir.", "tool_calls": []}


class _Registry:
    def schemas(self):
        return []

    def call(self, name, args):  # pragma: no cover
        return ""


def _agent(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    return Agent(Settings(), registry=_Registry(), session_id="s", llm=_PlainLLM())


@pytest.mark.parametrize("question", [
    "What's on my calendar today?",
    "what's my next meeting?",
    "check my inbox for anything new",
    "any reminders for me today?",
])
def test_tool_backed_personal_questions_reach_the_model(tmp_path, monkeypatch, question):
    """The honesty intercept must NOT deflect tool-answerable questions."""
    agent = _agent(tmp_path, monkeypatch)
    reply = agent.handle(question)
    assert "don't have that on record" not in reply.lower()


def test_genuine_unknown_personal_fact_still_honest(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    reply = agent.handle("what is my passport number?")
    assert "don't have that on record" in reply.lower()


@pytest.mark.parametrize("url", [
    "http://localhost:8788/", "http://127.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data",
    "http://192.168.1.1/router", "http://10.0.0.5/internal",
])
def test_ssrf_guard_blocks_private_hosts(url):
    assert _is_private_host(url)
    assert "private or local" in _fetch_url(url)


def test_ssrf_guard_allows_public_hosts():
    assert not _is_private_host("https://example.com")


def test_calculator_rejects_bignum_pow():
    with pytest.raises(ValueError):
        _calculator("(10**300) ** 999")
    assert _calculator("2 ** 10") == "1024"
    assert _calculator("999 ** 3") == str(999 ** 3)
