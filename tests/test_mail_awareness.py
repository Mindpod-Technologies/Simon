"""Mail self-awareness: persona advertises the mailbox; mail requests
route to a tool-capable tier."""
from __future__ import annotations

from simon.agent import Agent
from simon.config import Settings
from simon.llm import SMART_KEYWORDS


def test_persona_declares_mailbox(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    from simon import memory
    memory.init_db()
    settings = Settings()
    settings.simon_mailbox = "simon@mindpodtech.com"

    class QuietLLM:
        model = "fake"
        model_fast = None

        def chat(self, messages, tools=None, model=None):
            return {"content": "noted", "tool_calls": []}

    agent = Agent(settings, registry=None, session_id="t", llm=QuietLLM())
    messages = agent._build_messages("hello")
    system = messages[0]["content"]
    assert "simon@mindpodtech.com" in system
    assert "send_email" in system
    assert "read_recent_emails" in system
    assert "Never claim you cannot send or read mail" in system


def test_mail_keywords_route_smart():
    for kw in ("email", "mail", "inbox"):
        assert kw in SMART_KEYWORDS


def test_mail_refusal_is_detected(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    from simon import memory
    memory.init_db()

    class QuietLLM:
        model = "fake"
        model_fast = None

        def chat(self, messages, tools=None, model=None):
            return {"content": "noted", "tool_calls": []}

    agent = Agent(Settings(), registry=None, session_id="t", llm=QuietLLM())
    assert agent._looks_like_false_refusal(
        "I'm afraid I cannot send email on your behalf, sir.",
        "send an email to bob@example.com")
    assert agent._looks_like_false_refusal(
        "I don't have a mailbox to check, sir.",
        "anything new in my inbox?")
    assert not agent._looks_like_false_refusal(
        "I cannot send it — the mail server returned a 503, sir.",
        "what is the capital of France?")
