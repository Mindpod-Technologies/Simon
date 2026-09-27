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
    def __init__(self, extra_tools=()):
        self.executed = []
        self._tools = ["web_search", *extra_tools]

    def __contains__(self, name):
        return name in self._tools

    def schemas(self):
        return [{"type": "function", "function": {
            "name": n, "description": "fake tool",
            "parameters": {"type": "object", "properties": {}}}}
            for n in self._tools]

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


class SensitiveEmitterLLM(GreetingLLM):
    """Stonewalls the main turn, then emits a SENSITIVE call only under the
    completion gate's pressure to act — the exact bypass scenario."""

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        if self.calls == 1:
            return {"content": "On it, sir — sending that now.",
                    "tool_calls": []}
        return {"content": None, "tool_calls": [{
            "id": "c1", "name": "send_email",
            "arguments": {"to": "ops@mindpodtech.com",
                          "subject": "Eval check", "body": "gate test"}}]}


def test_gate_retry_never_executes_sensitive_call_raw(db, monkeypatch):
    """SECURITY: the gate pressures the model to act; if it responds with a
    sensitive call, that call must be PARKED for owner approval — never
    executed raw. 'Autonomous, not unsupervised' outranks receipts."""
    parked = []
    monkeypatch.setattr("simon.approvals.assess",
                        lambda name, args: "send an email")
    monkeypatch.setattr("simon.approvals.request",
                        lambda *a, **k: parked.append(a))
    registry = FakeRegistry()
    agent = _agent(SensitiveEmitterLLM(), registry, monkeypatch, MULTI_PLAN)
    reply = agent.handle(ASSIGNMENT)
    assert "approve" in reply.lower()       # approval ask, not execution
    assert parked                           # parked via approvals.request
    assert registry.executed == []          # NEVER ran raw


def test_explicit_email_command_parks_deterministically(db, monkeypatch):
    """An explicit, fully-specified email command must reach the approval
    ask regardless of model mood — no LLM call is even needed."""
    parked = []

    monkeypatch.setattr("simon.approvals.assess",
                        lambda name, args: f"send an email to {args['to']}")
    monkeypatch.setattr("simon.approvals.request",
                        lambda *a, **k: parked.append(a))
    registry = FakeRegistry(extra_tools=("send_email",))
    llm = GreetingLLM()  # must never be consulted for the control flow
    agent = _agent(llm, registry, monkeypatch, CHAT_PLAN)
    reply = agent.handle(
        "Simon, send an email right now to ops@mindpodtech.com with "
        "subject 'Eval check' and body 'approval gate test'.")
    assert "approve" in reply.lower()
    assert parked and parked[0][1] == "send_email"
    assert parked[0][2] == {"to": "ops@mindpodtech.com",
                            "subject": "Eval check",
                            "body": "approval gate test"}
    assert llm.calls == 0                   # zero model dependence
    assert registry.executed == []


def test_email_question_does_not_park(db, monkeypatch):
    """Negative control: 'did you send an email to X?' is a question, not a
    command — nothing may be parked."""
    parked = []
    monkeypatch.setattr("simon.approvals.request",
                        lambda *a, **k: parked.append(a))
    registry = FakeRegistry()
    agent = _agent(GreetingLLM(), registry, monkeypatch, CHAT_PLAN)
    agent.handle("did you send an email to bob@example.com?")
    assert parked == []


def test_multi_verb_request_gated_even_when_planner_absent(db, monkeypatch):
    """Planner stochasticity must not un-gate an unmistakable assignment:
    2+ distinct action verbs → receipts mandatory even with NO planner
    verdict. A bare greeting on such a turn is rejected."""
    registry = FakeRegistry()
    agent = _agent(GreetingLLM(), registry, monkeypatch, plan=None)
    reply = agent.handle(
        "Research daily-planning apps, then email me the findings")
    assert "Hello Jae" not in reply
    assert "nothing has been done" in reply.lower()


def test_single_verb_chat_not_gated(db, monkeypatch):
    """Negative control: one action word is conversation, not an assignment
    — 'send me a joke' must not trigger the gate."""
    registry = FakeRegistry()
    agent = _agent(GreetingLLM(), registry, monkeypatch, plan=None)
    reply = agent.handle("Send me a joke")
    assert reply.startswith("Hello Jae")


class DodgeThenDeliverLLM(GreetingLLM):
    """Burns an irrelevant tool call for a 'receipt', dodges, then delivers
    a substantive answer under gate pressure."""

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        if self.calls == 1:
            return {"content": None, "tool_calls": [{
                "id": "c1", "name": "web_search",
                "arguments": {"query": "daily planning apps"}}]}
        if self.calls == 2:
            return {"content": "Hello! How can I assist you today?",
                    "tool_calls": []}
        return {"content": "Bento vs Sunsama: " + "real comparison. " * 30,
                "tool_calls": []}


class DodgeForeverLLM(GreetingLLM):
    """Manufactures a receipt, then stonewalls every gate round."""

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        if self.calls == 1:
            return {"content": None, "tool_calls": [{
                "id": "c1", "name": "web_search", "arguments": {"query": "x"}}]}
        return {"content": "Hello! How can I assist you today?",
                "tool_calls": []}


def test_dodge_after_irrelevant_receipt_is_gated(db, monkeypatch):
    """A dodge reply with a manufactured receipt must still trigger the
    gate; a substantive grounded answer is then accepted."""
    registry = FakeRegistry()
    agent = _agent(DodgeThenDeliverLLM(), registry, monkeypatch, MULTI_PLAN)
    reply = agent.handle(ASSIGNMENT)
    assert "How can I assist" not in reply
    assert "Bento vs Sunsama" in reply


def test_persistent_dodge_fails_honestly(db, monkeypatch):
    registry = FakeRegistry()
    agent = _agent(DodgeForeverLLM(), registry, monkeypatch, MULTI_PLAN)
    reply = agent.handle(ASSIGNMENT)
    assert "How can I assist" not in reply
    assert "nothing has been done" in reply.lower()


class ToolThenDodgeLLM(GreetingLLM):
    """Runs a real tool, then asks what to do with the results instead of
    answering — the non-answer-after-receipts failure (live 2026-09-26)."""

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        if self.calls == 1:
            return {"content": None, "tool_calls": [{
                "id": "c1", "name": "web_search",
                "arguments": {"query": "bento app"}}]}
        if self.calls == 2:
            return {"content": "How would you like me to use this "
                               "information?", "tool_calls": []}
        return {"content": "Bento is a daily planning app that organizes "
                           "your day around a focus list.", "tool_calls": []}


def test_dodge_after_real_results_is_grounded(db, monkeypatch):
    """Receipts + dodge question → regenerate the answer from tool output."""
    registry = FakeRegistry()
    agent = _agent(ToolThenDodgeLLM(), registry, monkeypatch, CHAT_PLAN)
    reply = agent.handle("what is the Bento app?")
    assert "How would you like" not in reply
    assert "daily planning app" in reply


class DodgeEvenWhenGroundedLLM(GreetingLLM):
    """Calls a real tool, then dodges EVERY pass — the 2026-09-26 Telegram
    failure where even regeneration returned 'How can I help you today?'"""

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        if self.calls == 1:
            return {"content": None, "tool_calls": [{
                "id": "c1", "name": "web_search",
                "arguments": {"query": "bento"}}]}
        return {"content": "Hi! How can I help you today?", "tool_calls": []}


def test_final_sweep_delivers_raw_output_when_model_dodges(db, monkeypatch):
    """When every prose pass dodges, the user still gets the REAL data —
    verbatim tool output is the floor, a dodge never ships."""
    registry = FakeRegistry()
    agent = _agent(DodgeEvenWhenGroundedLLM(), registry, monkeypatch,
                   CHAT_PLAN)
    reply = agent.handle("what is the Bento app?")
    assert "How can I help" not in reply
    assert "Bento is a daily task-planning app" in reply


class FrontierRescueLLM(GreetingLLM):
    """Local tier dodges every gate round; the frontier brain acts."""

    model = "smart"
    model_fast = "fast"
    model_frontier = "frontier"
    frontier_enabled = True

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        if model == "frontier":
            if not any(m.get("role") == "tool" for m in messages):
                return {"content": None, "tool_calls": [{
                    "id": "f1", "name": "web_search",
                    "arguments": {"query": "Bento planning app"}}]}
            return {"content": "From the frontier: Bento is a daily "
                               "planning app built around a focus list. " * 5,
                    "tool_calls": []}
        return {"content": "Hello Jae! How can I help?", "tool_calls": []}


def test_gate_escalates_to_frontier_before_honest_failure(db, monkeypatch):
    """When the local tier cannot complete a tool-mandatory turn, the
    frontier model gets one shot with full tools BEFORE the honest-failure
    floor — hard assignments should complete, not apologise."""
    registry = FakeRegistry()
    agent = _agent(FrontierRescueLLM(), registry, monkeypatch, MULTI_PLAN)
    reply = agent.handle(ASSIGNMENT)
    assert registry.executed == [("web_search",
                                  {"query": "Bento planning app"})]
    assert "From the frontier" in reply
    assert "nothing has been done" not in reply.lower()


def test_frontier_absent_keeps_honest_floor(db, monkeypatch):
    """No frontier configured → the honest-failure floor stands."""
    registry = FakeRegistry()
    agent = _agent(GreetingLLM(), registry, monkeypatch, MULTI_PLAN)
    reply = agent.handle(ASSIGNMENT)
    assert "nothing has been done" in reply.lower()


class LoopForeverLLM(GreetingLLM):
    """Always calls a tool when tools are offered — exhausts the iteration
    budget mid-work; answers only the final no-tools shot."""

    def chat(self, messages, tools=None, model=None):
        self.calls += 1
        if tools:
            return {"content": None, "tool_calls": [{
                "id": f"c{self.calls}", "name": "web_search",
                "arguments": {"query": f"q{self.calls}"}}]}
        return {"content": "Final deliverable written from the receipts.",
                "tool_calls": []}


def test_exhausted_loop_with_receipts_still_delivers(db, monkeypatch):
    """Iteration budget spent mid-work must NOT produce the 'tied myself in
    knots' apology when receipts exist — the deliverable still ships."""
    registry = FakeRegistry()
    agent = _agent(LoopForeverLLM(), registry, monkeypatch, CHAT_PLAN)
    reply = agent.handle("what is the Bento app?")
    assert "Final deliverable" in reply
    assert not agent.last_turn_exhausted


def test_sanitize_tool_name_strips_leaked_markup(db, monkeypatch):
    """Models leak chat markup into tool-call names under context pressure
    ('assistant<|channel|>mcp_github_list_contents' seen live 2026-09-26)."""
    agent = _agent(GreetingLLM(), FakeRegistry(), monkeypatch, CHAT_PLAN)
    assert agent._sanitize_tool_name(
        "assistant<|channel|>web_search") == "web_search"
    assert agent._sanitize_tool_name("web_search") == "web_search"
    assert agent._sanitize_tool_name("garbage") == "garbage"
    assert agent._sanitize_tool_name(None) == ""


def _fake_schemas(names):
    return [{"type": "function", "function": {
        "name": n, "description": f"does {n.replace('_', ' ')}",
        "parameters": {"type": "object", "properties": {}}}}
        for n in names]


def test_prune_schemas_under_threshold_passthrough(db, monkeypatch):
    agent = _agent(GreetingLLM(), FakeRegistry(), monkeypatch, CHAT_PLAN)
    small = _fake_schemas(["a", "b", "c"])
    assert agent._prune_schemas(small, "hello") is small


def test_prune_schemas_keeps_core_named_and_relevant(db, monkeypatch):
    from simon.agent import _ALWAYS_TOOLS, _TOOL_PRUNE_KEEP
    agent = _agent(GreetingLLM(), FakeRegistry(), monkeypatch, CHAT_PLAN)
    names = list(_ALWAYS_TOOLS) + [f"mcp_bulk_tool_{i}" for i in range(60)]
    names.append("mcp_firecrawl_firecrawl_scrape")
    schemas = _fake_schemas(names)
    out = agent._prune_schemas(
        schemas, "please scrape this page and email me the result")
    out_names = [s["function"]["name"] for s in out]
    assert len(out) <= _TOOL_PRUNE_KEEP
    # core tools always survive
    assert "web_search" in out_names and "send_email" in out_names
    # relevant tools rank in (scrape + email wording matches)
    assert "mcp_firecrawl_firecrawl_scrape" in out_names
    # original registry order preserved
    assert out_names == sorted(out_names, key=lambda n: names.index(n))
