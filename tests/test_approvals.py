"""Approval gate: sensitive actions park until the owner says so.

Guards the Viktor-style contract — "autonomous, not unsupervised":
irreversible/externally visible actions ask before they run, and only an
explicit owner decision executes or cancels them.
"""

import pytest

from simon import approvals
from simon.agent import Agent
from simon.config import Settings


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def test_send_email_needs_approval():
    summary = approvals.assess(
        "send_email", {"to": "a@b.com", "subject": "Hi"})
    assert summary and "a@b.com" in summary


def test_destructive_shell_needs_approval():
    assert approvals.assess("run_shell", {"command": "rm -rf /tmp/x"})
    assert approvals.assess("run_shell", {"command": "git push origin main"})
    assert approvals.assess("run_shell", {"command": "pkill -f simon"})


def test_innocent_shell_passes():
    assert approvals.assess("run_shell", {"command": "ls -la"}) is None
    assert approvals.assess("run_shell", {"command": "git status"}) is None
    assert approvals.assess("run_shell", {"command": "echo hi"}) is None


def test_mcp_mutation_needs_approval_read_passes():
    assert approvals.assess("mcp_github_push_files", {"repo": "x"})
    assert approvals.assess("mcp_github_merge_pull_request", {})
    assert approvals.assess("mcp_stripe_create_charge", {})
    assert approvals.assess("mcp_github_get_file_contents", {}) is None
    assert approvals.assess("mcp_filesystem_read_file", {}) is None


def test_read_only_tools_pass():
    for name in ("read_file", "list_files", "web_search", "fetch_url",
                 "calculator", "recall_facts", "schedule_task",
                 "read_recent_emails"):
        assert approvals.assess(name, {}) is None


def test_delegate_and_smart_home_gated():
    assert approvals.assess("delegate_dev", {"task": "fix bug"})
    assert approvals.assess("ha_call_service",
                            {"service": "light.turn_on",
                             "entity_id": "light.den"})


# ---------------------------------------------------------------------------
# Reply classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["approve", "Approve!", "yes", "go ahead",
                                  "do it", "ok", "proceed", "ship it"])
def test_approve_phrases(text):
    assert approvals.classify_reply(text) == "approve"


@pytest.mark.parametrize("text", ["reject", "no", "nope", "cancel", "don't",
                                  "stop", "never mind"])
def test_reject_phrases(text):
    assert approvals.classify_reply(text) == "reject"


@pytest.mark.parametrize("text", ["", "what's the weather?", "approve of what",
                                  "tell me more", "yes I like apples today"])
def test_non_decisions(text):
    assert approvals.classify_reply(text) is None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    return tmp_path


def test_request_pending_resolve(db):
    approvals.request("s1", "send_email", {"to": "a@b.com"}, "send an email")
    pend = approvals.pending("s1")
    assert pend and pend["tool"] == "send_email"
    assert approvals.pending("other-session") is None
    approvals.resolve(pend["id"], "approved")
    assert approvals.pending("s1") is None


def test_new_request_supersedes_old(db):
    approvals.request("s1", "send_email", {}, "first")
    approvals.request("s1", "run_shell", {"command": "rm x"}, "second")
    pend = approvals.pending("s1")
    assert pend["summary"] == "second"


def test_decode_args_roundtrip(db):
    approvals.request("s1", "send_email", {"to": "x@y.z"}, "send")
    assert approvals.decode_args(approvals.pending("s1")) == {"to": "x@y.z"}


# ---------------------------------------------------------------------------
# Agent integration
# ---------------------------------------------------------------------------

class SensitiveLLM:
    """First turn: call send_email. Later turns: plain answer."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return {"content": None, "tool_calls": [{
                "id": "call_1", "name": "send_email",
                "arguments": {"to": "boss@corp.com", "subject": "Report",
                              "body": "..."}}]}
        return {"content": "All done, sir.", "tool_calls": []}


class MailRegistry:
    def __init__(self):
        self.executed = []

    def schemas(self):
        return [{"type": "function", "function": {
            "name": "send_email", "description": "Send mail.",
            "parameters": {"type": "object", "properties": {}}}}]

    def call(self, name, args):
        self.executed.append((name, args))
        return f"Email sent to {args.get('to')}"


@pytest.fixture()
def mail_agent(db):
    registry = MailRegistry()
    agent = Agent(Settings(), registry=registry, session_id="mail-test",
                  llm=SensitiveLLM())
    return agent, registry


def test_sensitive_action_parks_for_approval(mail_agent):
    agent, registry = mail_agent
    reply = agent.handle("email the report to my boss")
    assert registry.executed == []  # NOT executed without approval
    assert "approve" in reply.lower() and "reject" in reply.lower()
    pend = approvals.pending("mail-test")
    assert pend and pend["tool"] == "send_email"


def test_approve_executes_parked_action(mail_agent):
    agent, registry = mail_agent
    agent.handle("email the report to my boss")
    reply = agent.handle("approve")
    assert registry.executed == [("send_email",
                                  {"to": "boss@corp.com",
                                   "subject": "Report", "body": "..."})]
    assert approvals.pending("mail-test") is None
    assert reply  # summary delivered


def test_reject_cancels_parked_action(mail_agent):
    agent, registry = mail_agent
    agent.handle("email the report to my boss")
    reply = agent.handle("reject")
    assert registry.executed == []
    assert "stood down" in reply.lower()
    assert approvals.pending("mail-test") is None


def test_changing_subject_keeps_pending_with_reminder(mail_agent):
    agent, registry = mail_agent
    agent.handle("email the report to my boss")
    reply = agent.handle("tell me something else entirely")
    assert registry.executed == []
    assert "still awaiting" in reply.lower()
    assert approvals.pending("mail-test") is not None


def test_approvals_disabled_executes_immediately(db):
    registry = MailRegistry()
    settings = Settings(simon_approvals_enabled=False)
    agent = Agent(settings, registry=registry, session_id="off-test",
                  llm=SensitiveLLM())
    reply = agent.handle("email the report to my boss")
    assert registry.executed != []
    assert "approve" not in reply.lower()


# ---------------------------------------------------------------------------
# Cross-channel approval
# ---------------------------------------------------------------------------

def test_classify_decision_with_id():
    assert approvals.classify_decision("approve 3") == ("approve", 3)
    assert approvals.classify_decision("reject #12") == ("reject", 12)
    assert approvals.classify_decision("approve") == ("approve", None)
    assert approvals.classify_decision("what's for lunch?") == (None, None)


def test_cross_channel_approve_executes_other_sessions_pending(mail_agent):
    agent, registry = mail_agent
    agent.handle("email the report to my boss")      # parks in "mail-test"
    pend = approvals.pending("mail-test")
    assert pend is not None

    # A different conversation (e.g. Telegram) answers the ask.
    other = Agent(Settings(), registry=registry, session_id="telegram-42",
                  llm=SensitiveLLM())
    reply = other.handle("approve")
    assert registry.executed and registry.executed[0][0] == "send_email"
    assert approvals.pending("mail-test") is None
    assert reply


def test_cross_channel_reject_cancels_other_sessions_pending(mail_agent):
    agent, registry = mail_agent
    agent.handle("email the report to my boss")
    other = Agent(Settings(), registry=registry, session_id="slack-U1",
                  llm=SensitiveLLM())
    reply = other.handle("reject")
    assert registry.executed == []
    assert "stood down" in reply.lower()


def test_unknown_approval_id_politely_refused(mail_agent):
    agent, registry = mail_agent
    agent.handle("email the report to my boss")
    other = Agent(Settings(), registry=registry, session_id="telegram-42",
                  llm=SensitiveLLM())
    reply = other.handle("approve 99")
    assert registry.executed == []
    assert "no pending decision #99" in reply.lower()
    assert approvals.pending("mail-test") is not None  # still parked


def test_multiple_pending_asks_for_a_number(db):
    registry = MailRegistry()
    approvals.request("web-a", "send_email", {}, "send the invoice")
    approvals.request("job-9", "run_shell", {"command": "rm -rf /tmp/z"},
                      "run a destructive shell command")
    agent = Agent(Settings(), registry=registry, session_id="telegram-42",
                  llm=SensitiveLLM())
    reply = agent.handle("approve")
    assert registry.executed == []
    assert "#" in reply and "approve <number>" in reply.lower()
    # …and a numbered decision settles exactly one
    invoice = next(p for p in approvals.list_pending()
                   if "invoice" in p["summary"])
    reply2 = agent.handle(f"approve {invoice['id']}")
    assert registry.executed and registry.executed[0][0] == "send_email"
    remaining = approvals.list_pending()
    assert len(remaining) == 1 and "shell" in remaining[0]["summary"]
