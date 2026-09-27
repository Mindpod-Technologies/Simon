"""Agent loop: conversation management, tool dispatch, persona injection."""

from __future__ import annotations

import datetime
import functools
import json
import logging
import re
import time
from typing import Any, Optional

from . import memory, obs
from .config import Settings, get_settings
from .persona import SIMON_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_PLAN_HINT_RE = re.compile(
    r"\b(research|report|design|prd|email|send|file|document|"
    r"spreadsheet|pdf|search|fetch|browse|navigate|schedule|automat|"
    r"create|build|write|draft|review|analyz|summariz|download|"
    r"upload|screenshot|calculat|inbox|calendar|meeting|remind|"
    r"github|repo|chart|slide|deck|translat|invoice|outreach|"
    r"campaign|deploy|publish|merge|delete|install)\b",
    re.IGNORECASE)

# Imperative email command with fully-specified parts — parked for owner
# approval deterministically, no model cooperation required. Present-tense
# imperative only; "did you send…" / "the email you sent" don't match.
_SEND_EMAIL_RE = re.compile(
    r"\bsend (?:an? )?e?-?mail (?:right now |now )?to "
    r"(?P<to>[\w.+-]+@[\w-]+(?:\.[\w-]+)+)"
    r"(?:\s+with subject ['\"](?P<subject>[^'\"]+)['\"])?"
    r"(?:\s+and body ['\"](?P<body>[^'\"]+)['\"])?",
    re.IGNORECASE)

# Short content-free replies that dodge an assignment instead of answering
# it — used by the completion gate (module scope; reused post-retry).
_DODGE_RE = re.compile(
    r"\b(how (can|may|might) i (help|assist|be of)|"
    r"what (would you like|can i do|shall we)|"
    r"how would you like|"
    r"how may i be of|ready to (help|assist|dive|get started)|"
    r"how can i assist|let me know what you'?d like|"
    r"would you like me to|"
    r"what you'?d like to (tackle|work on|do next))\b",
    re.IGNORECASE)

# Tool-schema pruning: with MCP servers connected, the full schema list is
# 120+ tools (~20k tokens) — enough to bury a small local model's attention
# until it echoes the instructions instead of answering (observed live
# 2026-09-26). The main loop sends a relevance-pruned set; the corrective
# paths (gate, nudges) keep the FULL list so no tool is ever unreachable.
_TOOL_PRUNE_THRESHOLD = 40
_TOOL_PRUNE_KEEP = 32

# Tools that must always be available regardless of the turn's wording.
_ALWAYS_TOOLS = frozenset({
    "web_search", "fetch_url", "calculator", "remember_fact", "recall_facts",
    "create_document", "send_email", "read_recent_emails", "read_email",
    "schedule_task", "list_schedules", "cancel_schedule", "start_job",
    "job_status", "cancel_job", "list_files", "read_file", "write_file",
    "take_note", "read_notes", "ingest_note", "get_datetime", "load_skill",
    "browser_goto", "joke", "spawn_agent", "list_agents", "agent_status",
    "agent_result", "cancel_agent", "create_chart",
})

MAX_ITERATIONS = 5

# Interactive-turn tracker: background model work (mail checks, scheduled
# tasks) yields to live user turns. On a single-GPU local setup every
# background job evicts the chat model (OLLAMA_MAX_LOADED_MODELS=1) and
# stalls the user's next message for a full model reload.
LAST_INTERACTIVE = {"started": 0.0, "finished": 0.0}
_BACKGROUND_INTERFACES = {"scheduler", "job", "eval"}


def interactive_session_active(window_s: float = 150.0) -> bool:
    """True when a user turn is in flight or ended within ``window_s``."""
    if LAST_INTERACTIVE["started"] > LAST_INTERACTIVE["finished"]:
        return True  # a turn is running right now
    return (time.time() - LAST_INTERACTIVE["finished"]) < window_s


def _track_interactive(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        interactive = (getattr(self, "interface", "")
                       not in _BACKGROUND_INTERFACES)
        if interactive:
            LAST_INTERACTIVE["started"] = time.time()
        try:
            return fn(self, *args, **kwargs)
        finally:
            if interactive:
                LAST_INTERACTIVE["finished"] = time.time()
    return wrapper

# Matches replies that CLAIM a completed action ("I have set up …", "the
# automation is now active", "I have now called the tool …"). Used by the
# claimed-action guard to catch narration-without-tool-call.
_CLAIM_RE = re.compile(
    r"\b(has been|is now|'ve|have|having) (scheduled|activated|created|"
    r"queued|recorded|saved|sent|delivered|set up|set|established|arranged|"
    r"added|configured|enabled|booked|prepared|filed|stored|updated|called|"
    r"invoked|executed|completed|run|reviewed|read|visited|checked|"
    r"inspected|examined|fetched|analysed|analyzed|retrieved|looked at)\b",
    re.IGNORECASE)

# Matches fabricated tool output: the model invents a result block for a
# tool it never called ("Result from mcp_filesystem_get_file_info: …").
_FABRICATED_RESULT_RE = re.compile(
    r"\b(result|output|response) (from|of) [a-z_][a-z0-9_]{2,}\b",
    re.IGNORECASE)

# Matches adjective-style completion claims with no auxiliary verb:
# "Recurring automation scheduled.", "Reminder set.", "Document created."
_ADJECTIVE_CLAIM_RE = re.compile(
    r"\b(automation|schedule|task|reminder|job|appointment|event|document|"
    r"file|note|fact|entry|report)(?:'s| is| has been)?(?: now)?"
    r" (scheduled|created|activated|set up|saved|recorded|added|booked|"
    r"queued|sent|delivered|filed|stored)\b",
    re.IGNORECASE)

# Matches present-continuous action claims: "I am starting a background
# job", "I'm now queuing the task" — again, without any tool call.
_PRESENT_CLAIM_RE = re.compile(
    r"\b(i am|i'm) (now )?(starting|creating|scheduling|queuing|setting up|"
    r"initiating|launching|kicking off|saving|recording|sending)\b",
    re.IGNORECASE)

# Matches claims of having fetched external content when nothing was
# fetched: "Upon reviewing the page…", "The website clearly states…",
# "According to the link you provided…". Fourth variant of the narration
# disease — invented website contents.
_FETCH_CLAIM_RE = re.compile(
    r"\b(upon (reviewing|reading|visiting|checking|inspecting)|"
    r"having (reviewed|read|visited|checked|inspected)|"
    r"the (website|web ?page|page|site|link|url)( you (provided|shared|"
    r"gave|linked))?( clearly)? (states|says|describes|mentions|shows|"
    r"confirms|indicates)|"
    r"according to the (website|web ?page|page|site|link|url))\b",
    re.IGNORECASE)

# Matches presentation claims for visual/file artifacts that no tool
# produced: "Here is the pie chart showing…", "here's your graph",
# "the chart below". Fifth variant of the narration disease — the model
# claims to PRESENT a chart/document it never created.
_PRESENTS_ARTIFACT_RE = re.compile(
    r"\b(here(?: is|'s) (?:the |your |a |this )?(?:newly created )?"
    r"(?:pie |bar |line |scatter )?(?:chart|graph|plot|diagram|image|"
    r"spreadsheet|document|report)\b|"
    r"the (?:chart|graph|plot|diagram) (?:above|below)|"
    r"(?:pie |bar |line |scatter )?(?:chart|graph|plot|diagram)\s+"
    r"(?:is\s+)?(?:now\s+)?(?:rendered|displayed|presented|attached|"
    r"above|below)\b)",
    re.IGNORECASE)

# Cap on claimed-action re-nudges. Each nudge is a full smart-model
# generation (~30-60 s on local hardware), so the worst-case added latency
# stays bounded; after this many failures the honesty fallback replies.
NUDGE_MAX = 2


class Agent:
    """Conversational agent driving an LLM with a tool registry.

    The agent persists conversation history via :mod:`simon.memory`, injects
    the Simon persona system prompt (with today's date), and runs a
    tool-call loop of at most 8 iterations. Tool errors never propagate —
    they are returned to the LLM as tool results.
    """

    def __init__(self, settings: Optional[Settings] = None,
                 registry: Any = None, session_id: str = "default",
                 llm: Any = None, interface: str = "unknown") -> None:
        """Create an agent.

        ``llm`` defaults to a real :class:`simon.llm.LLM` built from
        settings; tests may inject a fake. ``registry`` defaults to
        :func:`simon.tools.build_default_registry`. ``interface`` tags
        observability events (web / slack / telegram / scheduler …).
        """
        self.settings = settings or get_settings()
        self.session_id = session_id
        self.interface = interface
        # Per-turn receipts, exposed for background harnesses (jobs,
        # scheduler) so they can enforce "no receipts → no deliverable".
        self.last_turn_tools: list[str] = []
        self.last_turn_exhausted = False
        if llm is not None:
            self.llm = llm
        else:
            from .llm import LLM
            self.llm = LLM(self.settings)
        if registry is not None:
            self.registry = registry
        else:
            from .tools import build_default_registry
            self.registry = build_default_registry(self.settings)

    @_track_interactive
    def handle(self, user_text: str) -> str:
        """Handle one user turn; record an 'error' event if the turn crashes."""
        from .context import current_session
        token = current_session.set(self.session_id)
        try:
            return self._handle(user_text)
        except Exception as exc:
            logger.exception("Agent turn crashed")
            obs.record_event(
                "error", interface=self.interface,
                session_id=self.session_id,
                error=repr(exc), stage="handle",
                user_text=user_text[:200])
            raise
        finally:
            current_session.reset(token)

    def _handle(self, user_text: str) -> str:
        """Handle one user turn and return Simon's final reply text."""
        memory.init_db()
        memory.add_message(self.session_id, "user", user_text)

        messages = self._build_messages(user_text)
        schemas = self.registry.schemas() if self.registry else None
        # Pruned schema view for local tiers — full schemas stay available
        # to the frontier model and to every corrective path below.
        turn_schemas = self._prune_schemas(schemas, user_text)

        # Capability questions ("what can you do?") get an instant,
        # deterministic answer built from the live registry — local models
        # hallucinate these, and the truth is knowable for free.
        from . import capabilities
        if capabilities.is_capability_question(user_text):
            reply = capabilities.capabilities_answer(schemas)
            memory.add_message(self.session_id, "assistant", reply)
            obs.record_event(
                "turn", interface=self.interface,
                session_id=self.session_id, model="(none)",
                route_reason="capabilities overview", latency_ms=0,
                tools=[], tool_errors=0, escalated=False,
                reply_len=len(reply), user_len=len(user_text))
            return reply

        # Deterministic honesty intercept: a personal question whose topic has
        # NEVER appeared in facts or conversation history gets a guaranteed
        # honest answer. Abliterated local models cannot be trusted not to
        # fabricate personal details (passwords, sizes, dates) — no prompt
        # rule is reliable against in-context priming.
        low = user_text.lower()
        personal = re.search(r"\b(my|our|we|i)\b", low) and re.search(
            r"\b(what|when|where|who|which|how)\b", low)
        # File/tool-intent questions ("what files are on my desktop?") are
        # NOT personal-trivia questions — tools answer them with ground
        # truth, so the intercept must not deflect them. The same holds for
        # tool-backed personal data: calendar, mail, and smart home.
        file_intent = any(k in low for k in (
            "file", "folder", "directory", "desktop", "document",
            "download", "spreadsheet", "pdf", "screenshot",
            "calendar", "meeting", "event", "appointment", "schedule",
            "email", "e-mail", "mail", "inbox",
            "thermostat", "lights", "light ", "temperature",
            "reminder", "remind"))
        if personal and not file_intent:
            try:
                fact_hits = memory.search_facts(user_text)
            except Exception:  # pragma: no cover
                fact_hits = []
            topic = memory.significant_words(user_text)
            # Only real conversation history counts as "discussed" — never
            # the system prompt (messages[0]), whose persona examples and
            # injected facts would otherwise neutralise this intercept.
            history_text = " ".join(
                str(m.get("content") or "") for m in messages[1:-1]).lower()
            discussed = any(w in history_text for w in topic)
            if not fact_hits and topic and not discussed:
                reply = ("I don't have that on record, sir — tell me and I "
                         "shall remember it.")
                memory.add_message(self.session_id, "assistant", reply)
                obs.record_event(
                    "turn", interface=self.interface,
                    session_id=self.session_id, model="(none)",
                    route_reason="honesty intercept", latency_ms=0,
                    tools=[], tool_errors=0, escalated=False,
                    reply_len=len(reply), user_len=len(user_text))
                return reply

        # Assignment cancellation (Core 2.0 M2): "never mind", "forget it"
        # and friends deterministically close the session's open assignment
        # — the commitment ends when the OWNER says so, not when the model
        # loses track of it.
        if re.search(
                r"\b(never\s?mind|forget (it|that|about it)|"
                r"cancel (that|this|it|the task|the assignment)|"
                r"drop (it|that)|stop (that|working on (it|that)))\b", low):
            try:
                from . import assignments
                open_asg = assignments.active(self.session_id)
            except Exception:  # pragma: no cover - never break a turn
                open_asg = None
            if open_asg:
                assignments.close(open_asg["id"], assignments.CANCELLED)
                reply = (f"Very well, sir — I've set aside the assignment: "
                         f"{open_asg['goal'][:120]}. Consider it cancelled.")
                memory.add_message(self.session_id, "assistant", reply)
                obs.record_event(
                    "turn", interface=self.interface,
                    session_id=self.session_id, model="(none)",
                    route_reason="assignment cancelled", latency_ms=0,
                    tools=[], tool_errors=0, escalated=False,
                    reply_len=len(reply), user_len=len(user_text))
                return reply

        # Model router: pick which brain handles this turn. No-op when the
        # router is disabled or a test injects a fake LLM.
        model = None
        route_model = getattr(self.llm, "route_model", None)
        if callable(route_model):
            try:
                hits = len(memory.search_facts(user_text))
            except Exception:
                hits = 0
            model = route_model(user_text, history_len=len(messages),
                                memory_hits=hits)
            # A message that names an uploaded/created document is document
            # work — the fast brain ignores injected excerpts, so always
            # escalate to the smart model.
            try:
                if model == getattr(self.llm, "model_fast", None):
                    from . import rag
                    if rag.chunks_for_mention(user_text, k=1):
                        model = self.llm.model
                        self.llm.last_route_reason = "named document"
            except Exception:  # pragma: no cover - never break a turn
                pass

        # Frontier tier: an explicit request ("use kimi", "ask the frontier
        # model") sends the whole turn to the cloud model when configured.
        wants_frontier = getattr(self.llm, "wants_frontier", None)
        if (callable(wants_frontier)
                and getattr(self.llm, "frontier_enabled", False)
                and wants_frontier(user_text)):
            model = self.llm.model_frontier
            self.llm.last_route_reason = "explicit frontier request"
            logger.info("router: explicit frontier request → %s", model)

        # Naming a tool means the user expects a real call — the fast tier
        # runs tool-less, so a tool-naming turn must go to the smart model.
        if model is not None and model == getattr(self.llm, "model_fast", None):
            low_text = user_text.lower()
            try:
                if any(s["function"]["name"] in low_text
                       for s in (schemas or [])):
                    model = self.llm.model
                    self.llm.last_route_reason = "named tool"
            except Exception:  # pragma: no cover - never break a turn
                pass
            # Explicit research intent ("do a web search", "look it up")
            # needs tools too — the fast tier would otherwise claim it
            # "cannot search" and then fabricate an answer from thin air.
            if (model is not None
                    and model == getattr(self.llm, "model_fast", None)
                    and re.search(
                        r"\b(web[ -]?search|search the (web|internet)|"
                        r"google (it|for|that)|look (it|that|this|her|him)"
                        r"\s?up|look up)\b", low_text)):
                model = self.llm.model
                self.llm.last_route_reason = "explicit search request"

        # PLAN verdict (Core 2.0 completion gate): a typed decision BEFORE
        # the turn runs — does this require tools, and is it multi-step?
        # A confident verdict forces the smart tier AND makes tool receipts
        # mandatory before the turn may deliver an answer. Cheap hint first:
        # obvious chat never pays for the planner model call.
        tool_mandatory = False
        plan_verdict = None
        hint_hits = {m.group(0).lower()
                     for m in _PLAN_HINT_RE.finditer(user_text)}
        if hint_hits:
            try:
                from . import decisions
                plan_verdict = decisions.plan_turn(user_text)
            except Exception:  # pragma: no cover - never break a turn
                plan_verdict = None
        if (plan_verdict
                and plan_verdict.get("confidence", 0) >= 0.65
                and plan_verdict.get("tools_required")):
            tool_mandatory = True
            if plan_verdict.get("multi_step"):
                model = self.llm.model
                self.llm.last_route_reason = (
                    f"plan multi-step ({plan_verdict['confidence']:.2f})")
                logger.info("planner: multi-step assignment → smart tier + "
                            "receipts mandatory (%.2f)",
                            plan_verdict["confidence"])
                # M2: record the commitment durably — history trimming must
                # never be able to amputate an accepted assignment.
                try:
                    from . import assignments
                    assignments.open_assignment(self.session_id, user_text)
                except Exception:  # pragma: no cover - never break a turn
                    pass
        elif len(hint_hits) >= 2:
            # The planner is a stochastic model call — when it is unsure,
            # unavailable, or WRONG about an unmistakably multi-action
            # request ("research X, then email me Y"), receipts are still
            # mandatory. A single action word stays chat-friendly ("send me
            # a joke" must not gate); two or more is an assignment.
            tool_mandatory = True
            logger.info("planner unsure/absent but %d action verbs — "
                        "receipts mandatory anyway", len(hint_hits))

        # Unified frontier handoff: multi-step per the planner OR
        # unmistakably multi-action by verb count (≥2) — small local tiers
        # flail on these either way (wrong args, dodges — observed live).
        # The planner saying "tool_single" about a 3-verb assignment must
        # not keep it local. Single-action tool turns stay local.
        if (tool_mandatory and getattr(self.llm, "frontier_enabled", False)
                and (len(hint_hits) >= 2
                     or (plan_verdict and plan_verdict.get("multi_step")))):
            model = self.llm.model_frontier
            self.llm.last_route_reason = "multi-action → frontier"
            logger.info("multi-action assignment → frontier tier (%s)",
                        model)

        # Approval gate ("autonomous, not unsupervised"): a previous turn may
        # have parked a sensitive action awaiting the owner's decision.
        # "approve" executes the stored call for real; "reject" cancels it;
        # anything else is treated as a new turn, with a reminder appended.
        from . import approvals
        approvals_on = getattr(
            getattr(self, "settings", None), "simon_approvals_enabled", True)
        existing_pending = None
        if approvals_on:
            try:
                existing_pending = approvals.pending(self.session_id)
            except Exception:  # pragma: no cover - never break a turn
                existing_pending = None

        # Cross-channel approval: nothing parked in THIS conversation, but
        # the user replied with a decision word — the ask may have come from
        # another surface (a background job, the web UI while they're on
        # Telegram). One pending → decide it; several → ask for the number.
        cross_target = None
        if approvals_on and existing_pending is None:
            decision, decision_id = approvals.classify_decision(user_text)
            if decision:
                try:
                    others = approvals.list_pending()
                except Exception:  # pragma: no cover - never break a turn
                    others = []
                if decision_id is not None:
                    cross_target = next(
                        (p for p in others if p["id"] == decision_id), None)
                    if cross_target is None:
                        reply = (f"I have no pending decision "
                                 f"#{decision_id} on record, sir — it may "
                                 f"have expired or already been settled.")
                        memory.add_message(
                            self.session_id, "assistant", reply)
                        obs.record_event(
                            "turn", interface=self.interface,
                            session_id=self.session_id, model="(none)",
                            route_reason="approval id unknown", latency_ms=0,
                            tools=[], tool_errors=0, escalated=False,
                            reply_len=len(reply), user_len=len(user_text))
                        return reply
                elif len(others) == 1:
                    cross_target = others[0]
                elif len(others) > 1:
                    listing = "\n".join(
                        f"  #{p['id']} — {p['summary'][:70]}"
                        for p in others[:5])
                    reply = (f"Several matters await your decision, sir:\n"
                             f"{listing}\nReply **approve <number>** or "
                             f"**reject <number>** to settle one.")
                    memory.add_message(self.session_id, "assistant", reply)
                    obs.record_event(
                        "turn", interface=self.interface,
                        session_id=self.session_id, model="(none)",
                        route_reason="approval disambiguation", latency_ms=0,
                        tools=[], tool_errors=0, escalated=False,
                        reply_len=len(reply), user_len=len(user_text))
                    return reply

        target = existing_pending or cross_target
        if approvals_on and target:
            decision, _ = approvals.classify_decision(user_text)
            if decision == "reject":
                approvals.resolve(target["id"], "rejected")
                reply = (f"Very well, sir — I've stood down on: "
                         f"{target['summary']}. It shall not be "
                         f"done.")
                memory.add_message(self.session_id, "assistant", reply)
                obs.record_event(
                    "turn", interface=self.interface,
                    session_id=self.session_id, model="(none)",
                    route_reason="approval rejected", latency_ms=0,
                    tools=[], tool_errors=0, escalated=False,
                    reply_len=len(reply), user_len=len(user_text))
                return reply
            if decision in ("approve", "approve_always"):
                approvals.resolve(target["id"], "approved")
                grant_note = ""
                if decision == "approve_always":
                    grant_key = approvals.add_grant(
                        target["tool"], approvals.decode_args(target),
                        created_by=self.session_id)
                    grant_note = (
                        f"\n\nNoted — I've granted standing approval for "
                        f"this exact operation (`{grant_key}`). I shall not "
                        f"ask again unless it changes; you can review or "
                        f"revoke it in Settings → Standing approvals.")
                    logger.info("standing grant created: %s", grant_key)
                logger.info("approval granted — executing %s",
                            target["tool"])
                t0 = time.monotonic()
                tools_used = [target["tool"]]
                tool_errors = 0
                artifact_markers: list[str] = []
                try:
                    output = self.registry.call(
                        target["tool"],
                        approvals.decode_args(target))
                    if str(output).startswith("Error"):
                        tool_errors = 1
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception("approved tool %s raised",
                                     target["tool"])
                    output = (f"Error: tool {target['tool']} "
                              f"failed: {exc}")
                    tool_errors = 1
                for marker in re.findall(r"\[artifact:([^\]\s]+)\]",
                                         str(output or "")):
                    if marker not in artifact_markers:
                        artifact_markers.append(marker)
                ground_messages = messages + [
                    {"role": "user", "content": (
                        f"[System note: the user APPROVED the pending "
                        f"action. The system has executed "
                        f"{target['tool']} FOR you — this is its "
                        f"real output. Confirm the result to the user from "
                        f"it; do not call more tools.\n\n{output}]")},
                ]
                try:
                    result = self.llm.chat(ground_messages)
                    reply = (result.get("content") or "").strip()
                except Exception:
                    logger.exception("approval summary failed")
                    reply = ""
                if not reply:
                    reply = (f"Done, sir — as approved, I carried out: "
                             f"{target['summary']}.\n\n{output}")
                if self._looks_like_false_refusal(reply, user_text):
                    reply = (f"Done, sir — as approved, the raw output of "
                             f"{target['tool']}:\n\n{output}")
                missing = [p for p in artifact_markers if p not in reply]
                if missing:
                    reply = reply.rstrip() + "\n" + "\n".join(
                        f"[artifact:{p}]" for p in missing)
                reply += grant_note
                self.last_turn_tools = list(tools_used)
                # An approved action with real receipts settles any standing
                # assignment for this session.
                try:
                    from . import assignments
                    open_asg = assignments.active(self.session_id)
                    if open_asg:
                        assignments.record_receipts(open_asg["id"], tools_used)
                        assignments.close(open_asg["id"], assignments.DONE)
                except Exception:  # pragma: no cover - never break a turn
                    pass
                memory.add_message(self.session_id, "assistant", reply)
                obs.record_event(
                    "turn", interface=self.interface,
                    session_id=self.session_id,
                    model=getattr(self.llm, "model", ""),
                    route_reason="approved action executed",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    tools=tools_used, tool_errors=tool_errors,
                    escalated=False, reply_len=len(reply),
                    user_len=len(user_text))
                return reply

        reply = ""
        tool_errors = 0
        tools_used: list[str] = []
        tool_outputs: list[str] = []
        failed_calls: dict[str, int] = {}
        artifact_markers: list[str] = []
        escalated = False
        approval_hold = False
        regenerated = False
        self.last_turn_exhausted = False
        self.last_turn_tools = []
        t0 = time.monotonic()

        def _collect_artifact_markers(output) -> None:
            """Remember [artifact:path] markers from tool output so they
            always reach the final reply, even when the model paraphrases
            or the tool ran inside a nudge loop (whose messages live in a
            throwaway copy)."""
            for marker in re.findall(r"\[artifact:([^\]\s]+)\]",
                                     str(output or "")):
                if marker not in artifact_markers:
                    artifact_markers.append(marker)
            # Keep the raw successful output too — the final dodge sweep
            # delivers REAL data verbatim when every prose attempt fails.
            text = str(output or "")
            if text and not text.startswith("Error"):
                tool_outputs.append(text)

        def _guarded_call(name, args):
            """Execute a tool from a rescue/nudge path, unless it needs owner
            approval — sensitive actions may only run through the main loop's
            approval ask (or an explicit 'approve'), never via a nudge."""
            if approvals_on:
                try:
                    if approvals.assess(name, args):
                        return ("Error: this action is sensitive and needs "
                                "the owner's approval. Do NOT attempt it here "
                                "— end your turn by asking the user to "
                                "confirm it directly.")
                except Exception:  # pragma: no cover - never break a turn
                    pass
            return self.registry.call(name, args)

        # Deterministic explicit-tool execution: "use the list_files tool on
        # /path" runs the tool directly and has the model summarise the real
        # output. Cautious local models sometimes REFUSE permitted file ops
        # even after nudges — when the owner asks explicitly, the system
        # guarantees the action, the model only words the answer.
        explicit = self._explicit_tool_call(user_text)
        if explicit is not None and explicit[0] in self.registry:
            tname, targs = explicit
            logger.info("explicit tool request — running %s directly", tname)
            try:
                output = self.registry.call(tname, targs)
                _collect_artifact_markers(output)
                tools_used.append(tname)
                ground_messages = messages + [
                    {"role": "user", "content": (
                        f"[System note: the user explicitly asked you to run "
                        f"the {tname} tool. The system has executed it FOR "
                        f"you with the exact arguments requested — this is "
                        f"its real output. Answer the user's request "
                        f"strictly from it; do not call more tools.\n\n"
                        f"{output}]")},
                ]
                if model is None:
                    result = self.llm.chat(ground_messages)
                else:
                    result = self.llm.chat(ground_messages, model=model)
                reply = (result.get("content") or "").strip()
                if self._looks_like_false_refusal(reply, user_text):
                    # The model refused despite holding the real output —
                    # the owner asked explicitly, so deliver the data raw.
                    reply = (f"As requested, sir — the raw output of "
                             f"{tname}:\n\n{output}")
                regenerated = True
            except Exception:
                logger.exception("explicit tool execution failed")
                reply = ""

        # Deterministic approval path for an explicit email command:
        # "send an email to X with subject '...' and body '...'" must ALWAYS
        # reach the owner's approval ask — never depend on whether the model
        # feels like emitting the call this run. "Autonomous, not
        # unsupervised" is not subject to model mood. Past-tense questions
        # ("did you send…") don't match the imperative pattern.
        if not reply and approvals_on:
            m = _SEND_EMAIL_RE.search(user_text)
            if (m and m.group("subject") and m.group("body")
                    and "send_email" in self.registry):
                args = {"to": m.group("to"),
                        "subject": m.group("subject"),
                        "body": m.group("body")}
                try:
                    needs = approvals.assess("send_email", args)
                except Exception:  # pragma: no cover - never break a turn
                    needs = None
                if needs:
                    logger.info("explicit email command — parking for "
                                "approval (%s)", args["to"])
                    approvals.request(self.session_id, "send_email", args,
                                      needs, interface=self.interface)
                    reply = (
                        f"One moment, sir — this one needs your say-so: "
                        f"I'd like to **{needs}**. Reply **approve** and "
                        f"I shall do it at once, **approve always** to "
                        f"grant this exact operation going forward, or "
                        f"**reject** and I'll stand down.")
                    memory.add_message(self.session_id, "assistant", reply)
                    obs.record_event(
                        "turn", interface=self.interface,
                        session_id=self.session_id,
                        model="(none)", route_reason="explicit email approval",
                        latency_ms=int((time.monotonic() - t0) * 1000),
                        tools=[], tool_errors=0, escalated=False,
                        reply_len=len(reply), user_len=len(user_text))
                    return reply

        fast_model = getattr(self.llm, "model_fast", None)
        # Frontier turns do heavy multi-step work (research → fetches →
        # documents); the local-sized budget of 5 iterations ran out
        # mid-write-up on a live job (2026-09-26). Double it for frontier.
        max_iters = MAX_ITERATIONS
        if model and model == getattr(self.llm, "model_frontier", None):
            max_iters = MAX_ITERATIONS * 2
        for _ in range(0 if reply else max_iters):
            # Fast tier handles simple turns without tools: sending the full
            # tool schemas (~18k tokens with MCP servers connected) would
            # dominate its latency. Tool-needing turns route smart via the
            # classifier, and mid-turn escalation restores full schemas.
            # The smart local tier gets the RELEVANCE-PRUNED set — the full
            # 120+ tool list is enough to make a small model echo its
            # instructions instead of answering. Frontier gets everything.
            turn_tools = turn_schemas or None
            if fast_model and model == fast_model:
                turn_tools = None
            elif model and model == getattr(self.llm, "model_frontier", None):
                turn_tools = schemas or None
            try:
                if model is None:
                    result = self.llm.chat(messages, tools=turn_tools)
                else:
                    result = self.llm.chat(messages, tools=turn_tools,
                                           model=model)
            except Exception:
                # Local tier unreachable/failed (e.g. Ollama down): fall back
                # to the frontier model for the rest of this turn when one
                # is configured, rather than erroring out to the interface.
                frontier_model = getattr(self.llm, "model_frontier", "")
                if (getattr(self.llm, "frontier_enabled", False)
                        and model != frontier_model):
                    logger.warning("model call failed — escalating turn to "
                                   "frontier model %s", frontier_model,
                                   exc_info=True)
                    model = frontier_model
                    escalated = True
                    self.llm.last_route_reason = "local model failure"
                    continue
                raise
            tool_calls = result.get("tool_calls") or []
            for _c in tool_calls:
                _c["name"] = self._sanitize_tool_name(_c.get("name"))
            if not tool_calls:
                reply = result.get("content") or ""
                break

            # Record the assistant turn that requested the tool calls.
            messages.append({
                "role": "assistant",
                "content": result.get("content") or "",
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["arguments"]),
                        },
                    }
                    for call in tool_calls
                ],
            })

            # Execute each tool call; registry.call never raises, but guard
            # anyway so a misbehaving registry cannot kill the loop.
            for call in tool_calls:
                if approvals_on:
                    try:
                        needs = approvals.assess(call["name"],
                                                 call["arguments"])
                    except Exception:  # pragma: no cover - never break a turn
                        needs = None
                    if needs:
                        # Sensitive action — park it and ask the owner
                        # instead of executing. "Autonomous, not unsupervised."
                        approvals.request(self.session_id, call["name"],
                                          call["arguments"], needs,
                                          interface=self.interface)
                        reply = (
                            f"One moment, sir — this one needs your say-so: "
                            f"I'd like to **{needs}**. Reply **approve** and "
                            f"I shall do it at once, **approve always** to "
                            f"grant this exact operation going forward, or "
                            f"**reject** and I'll stand down.")
                        approval_hold = True
                        logger.info("approval requested for %s",
                                    call["name"])
                        break
                tools_used.append(call["name"])
                signature = call["name"] + json.dumps(call["arguments"],
                                                      sort_keys=True)
                if failed_calls.get(signature, 0) >= 1:
                    # Spiral guard: the model is re-issuing an exact call
                    # that already failed (e.g. read_file on an invented
                    # path). Refuse the repeat instead of looping.
                    output = ("Error: this exact tool call already failed "
                              "once this turn. Do NOT retry it — answer "
                              "from what you already have.")
                    tool_errors += 1
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": output,
                    })
                    continue
                try:
                    output = self.registry.call(call["name"], call["arguments"])
                    _collect_artifact_markers(output)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception("Tool %s raised unexpectedly", call["name"])
                    output = f"Error: tool {call['name']} failed: {exc}"
                if str(output).startswith("Error"):
                    tool_errors += 1
                    failed_calls[signature] = failed_calls.get(signature, 0) + 1
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": str(output),
                })

            if approval_hold:
                # A sensitive call was parked for owner approval — end the
                # turn here; the reply already asks approve/reject.
                break

            # Escalation: the fast brain is fumbling its tool calls — switch
            # to the smart model for the rest of this turn.
            fast = getattr(self.llm, "model_fast", None)
            if (model is not None and fast and model == fast
                    and tool_errors >= 2):
                logger.info("router: escalating turn to %s after %d tool "
                            "errors", self.llm.model, tool_errors)
                model = self.llm.model
                escalated = True
        else:
            # Loop exhausted on local tiers: give the frontier model one clean
            # shot at a final answer (no tools, so it cannot loop) before
            # apologising to the user. Skipped when the explicit-tool path
            # already answered (range(0) loop still triggers this else).
            frontier_model = getattr(self.llm, "model_frontier", "")
            if (not reply
                    and getattr(self.llm, "frontier_enabled", False)
                    and model != frontier_model):
                try:
                    result = self.llm.chat(messages, model=frontier_model)
                    candidate = (result.get("content") or "").strip()
                    if candidate:
                        reply = candidate
                        escalated = True
                        model = frontier_model
                        self.llm.last_route_reason = "frontier rescue"
                except Exception:
                    logger.exception("frontier rescue failed")
            if not reply and tool_outputs:
                # The work HAPPENED (receipts in context) but the iteration
                # budget ran out before the write-up — one clean no-tools
                # shot at the deliverable from what the tools produced.
                try:
                    fin = self.llm.chat(messages + [
                        {"role": "user", "content": (
                            "Your tool budget for this turn is now spent. "
                            "Using ONLY the tool results already in this "
                            "conversation, write the final deliverable for "
                            "the user now — complete and self-contained. "
                            "No new tool calls.")}],
                        model=model or self.llm.model)
                    cand = (fin.get("content") or "").strip()
                    if cand and not (len(cand) < 400
                                     and _DODGE_RE.search(cand)):
                        reply = cand
                except Exception:
                    logger.exception("final no-tools deliverable failed")
            if not reply and tool_outputs:
                # Deterministic floor: real receipts, delivered verbatim —
                # never an apology over completed work.
                reply = ("Here's what I completed, sir — straight from the "
                         "tools, as my write-up ran out of steps:\n\n"
                         + "\n\n".join(tool_outputs[-2:])[:3000])
            if not reply:
                reply = ("I do apologise, sir — I seem to have tied myself in "
                         "knots with tools. Perhaps we might try that again, "
                         "more simply?")
                self.last_turn_exhausted = True

        reply = reply or "Very good, sir. (No further response was required.)"

        # COMPLETION GATE (Core 2.0): a planner-marked tool-mandatory turn
        # may not deliver an answer with zero receipts. Six prose-regex
        # families tried to infer "did an action happen" from wording — the
        # gate is a boolean and format-proof (prose, JSON, or call syntax).
        # Dodge detector: on a tool-mandatory turn, a short content-free
        # reply ("How can I assist you today?") is a stonewall — even if the
        # model burned an irrelevant tool call to manufacture a "receipt".
        _dodge = bool(
            tool_mandatory and reply and len(reply.strip()) < 400
            and _DODGE_RE.search(reply))
        if (tool_mandatory and not approval_hold and reply
                and (not tools_used or _dodge)):
            if _dodge and tools_used:
                logger.warning("completion gate: dodge reply despite %d "
                               "receipt(s) — pressuring for the deliverable",
                               len(tools_used))
            # Name the candidate tools explicitly — an unnamed "call tools"
            # nudge left the model guessing which ones exist.
            _gate_tools = ", ".join(sorted(
                s["function"]["name"] for s in (schemas or [])
                if isinstance(s, dict) and "function" in s))[:600]
            _gate_hint = (f" Available tools include: {_gate_tools}."
                          if _gate_tools else "")
            if tools_used:
                _gate_msg = (
                    "SYSTEM GATE: that reply dodges the assignment. You "
                    "already hold REAL tool output in this conversation — "
                    "deliver the answer from it NOW, in full, or call more "
                    "tools if a step is genuinely undone. No greetings, no "
                    "offers to help — the deliverable itself." + _gate_hint)
            else:
                _gate_msg = (
                    "SYSTEM GATE: that reply completes nothing — the request "
                    "requires real tool actions and you called none. Call the "
                    "tools that actually perform the assignment NOW (one or "
                    "more real tool calls), or state plainly which exact step "
                    "you cannot do and why. Do not re-describe the task — "
                    "act on it." + _gate_hint)
            retry_messages = messages + [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": _gate_msg},
            ]
            receipts = list(tools_used)

            def _gate_exec(calls) -> str:
                """Execute gate-retry calls with approval parking.
                Returns 'parked' | 'receipts' | 'none'."""
                nonlocal reply, approval_hold
                made = False
                for call in calls[:5]:
                    # The gate pressures the model to act — under that
                    # pressure it may emit a SENSITIVE call. Executing that
                    # raw would bypass the approval gate entirely
                    # ("autonomous, not unsupervised"), so park it exactly
                    # like the main loop: the owner decides, the gate never
                    # overrides.
                    if approvals_on:
                        try:
                            needs = approvals.assess(call["name"],
                                                     call["arguments"])
                        except Exception:  # pragma: no cover - defensive
                            needs = None
                        if needs:
                            approvals.request(
                                self.session_id, call["name"],
                                call["arguments"], needs,
                                interface=self.interface)
                            reply = (
                                f"One moment, sir — this one needs your "
                                f"say-so: I'd like to **{needs}**. Reply "
                                f"**approve** and I shall do it at once, "
                                f"**approve always** to grant this exact "
                                f"operation going forward, or **reject** "
                                f"and I'll stand down.")
                            approval_hold = True
                            logger.info("completion gate parked sensitive "
                                        "call %s for approval", call["name"])
                            return "parked"
                    try:
                        output = self.registry.call(call["name"],
                                                    call["arguments"])
                    except Exception as exc:  # pragma: no cover
                        output = (f"Error: tool {call['name']} "
                                  f"failed: {exc}")
                    receipts.append(call["name"])
                    retry_messages.append({
                        "role": "tool", "tool_call_id": call["id"],
                        "content": str(output)})
                    made = True
                return "receipts" if made else "none"

            try:
                # Bounded loop, mirroring the claimed-action nudge: one shot
                # is not enough for a model that narrates instead of
                # emitting — press up to 3 rounds, blunter each time.
                for _gate_round in range(3):
                    result = self.llm.chat(retry_messages,
                                           tools=schemas or None,
                                           model=model or self.llm.model)
                    gate_calls = result.get("tool_calls") or []
                    for _c in gate_calls:
                        _c["name"] = self._sanitize_tool_name(
                            _c.get("name"))
                    if not gate_calls:
                        candidate = (result.get("content") or "").strip()
                        if candidate:
                            reply = candidate
                            retry_messages.append(
                                {"role": "assistant", "content": candidate})
                        if _gate_round < 2:
                            retry_messages.append({"role": "user", "content": (
                                "No — you again DESCRIBED the work without "
                                "emitting a tool call. Do not write about "
                                "tools; emit the actual tool_call now, or "
                                "plainly state you cannot.")})
                            continue
                        break
                    if _gate_exec(gate_calls) == "parked":
                        break
                    if approval_hold or len(receipts) > len(tools_used):
                        break
                if approval_hold:
                    pass  # the reply IS the approval ask — turn ends here
                else:
                    if len(receipts) > len(tools_used):
                        tools_used = receipts
                        result2 = self.llm.chat(retry_messages,
                                                model=model or self.llm.model)
                        candidate = (result2.get("content") or "").strip()
                        # A dodge summary is no summary at all — leave reply
                        # as-is so the grounded/frontier path below engages.
                        if candidate and not (
                                len(candidate) < 400
                                and _DODGE_RE.search(candidate)):
                            reply = candidate
                    # When the turn holds real tool output, a substantive
                    # non-dodge answer built from that output is grounded —
                    # accept it. Zero-receipt turns stay strict: prose cannot
                    # be trusted without evidence.
                    _grounded = bool(
                        tools_used and reply.strip()
                        and len(reply.strip()) >= 20
                        and not _DODGE_RE.search(reply))
                    if not _grounded:
                        # Last resort before admitting defeat: the frontier
                        # brain gets one shot at the assignment with the FULL
                        # tool list. Local tiers flail on multi-step work
                        # (wrong args, dodges); the cloud model usually
                        # doesn't.
                        frontier_model = getattr(self.llm, "model_frontier", "")
                        if (frontier_model
                                and getattr(self.llm, "frontier_enabled", False)
                                and model != frontier_model):
                            try:
                                logger.info("completion gate: escalating to "
                                            "frontier %s", frontier_model)
                                fres = self.llm.chat(
                                    retry_messages, tools=schemas or None,
                                    model=frontier_model)
                                fcalls = fres.get("tool_calls") or []
                                for _c in fcalls:
                                    _c["name"] = self._sanitize_tool_name(
                                        _c.get("name"))
                                if fcalls:
                                    if (_gate_exec(fcalls) == "receipts"
                                            and len(receipts)
                                            > len(tools_used)):
                                        tools_used = receipts
                                        result3 = self.llm.chat(
                                            retry_messages,
                                            model=frontier_model)
                                        cand = (result3.get("content")
                                                or "").strip()
                                        if cand:
                                            reply = cand
                                elif tools_used:
                                    cand = (fres.get("content")
                                            or "").strip()
                                    if (cand and len(cand) >= 300
                                            and not _DODGE_RE.search(cand)):
                                        reply = cand
                                if not approval_hold:
                                    escalated = True
                                    model = frontier_model
                                    self.llm.last_route_reason = (
                                        "gate → frontier")
                            except Exception:
                                logger.exception(
                                    "gate frontier escalation failed")
                    _grounded = bool(
                        tools_used and reply.strip()
                        and len(reply.strip()) >= 20
                        and not _DODGE_RE.search(reply))
                    if not _grounded and not approval_hold:
                        reply = (
                            "I must be straight with you, sir: that assignment "
                            "needs real actions, and I could not complete them "
                            "this turn — so nothing has been done. Ask me again "
                            "and I shall take it step by step.")
            except Exception:
                logger.exception("completion gate retry failed")

        # Results-grounding: tools ran and returned REAL output, yet the
        # reply dodges ("how would you like me to use this information?")
        # instead of answering the question that was asked. Regenerate
        # strictly from the tool output — the same guarantee as URL
        # grounding, generalized to any tool-backed turn.
        if (not approval_hold and not tool_mandatory and tools_used and reply
                and len(reply.strip()) < 400 and _DODGE_RE.search(reply)):
            logger.warning("dodge reply despite %d receipt(s) — grounding "
                           "from tool output", len(tools_used))
            ground_messages = messages + [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": (
                    "The tools above returned REAL results and the user "
                    "asked a direct question. Answer it NOW from those "
                    "results, in the format and length the user requested. "
                    "Do not ask what to do with the information, and do not "
                    "offer options — give the answer.")},
            ]
            try:
                if model is None:
                    result = self.llm.chat(ground_messages)
                else:
                    result = self.llm.chat(ground_messages, model=model)
                candidate = (result.get("content") or "").strip()
                if candidate and not _DODGE_RE.search(candidate):
                    reply = candidate
                    regenerated = True
            except Exception:
                logger.exception("results-grounding regeneration failed")

        # Deterministic URL grounding: when the user's message contains a URL
        # and no fetch-type tool ran this turn, do NOT rely on the model
        # choosing to fetch — fetch it ourselves and regenerate the reply
        # from the real content. The model summarises; the system guarantees
        # the grounding. (regenerated is initialised once at the top of the
        # turn — resetting it here would erase the explicit-tool path's mark.)
        _FETCH_TOOLS = {"fetch_url", "browser_goto", "web_search"}
        if approval_hold:
            # A sensitive action is parked awaiting the owner's decision —
            # the reply IS the approval ask; no grounding or rescue passes.
            pass
        elif not _FETCH_TOOLS.intersection(tools_used):
            url = self._extract_url(user_text)
            if url and "fetch_url" in self.registry:
                logger.info("URL in user message but no fetch — grounding "
                            "deterministically via fetch_url(%s)", url)
                fetched = self.registry.call("fetch_url", {"url": url})
                if str(fetched).startswith("Error"):
                    # A failed forced fetch must not hijack the turn: the
                    # user's actual request (research, a job, an email) still
                    # stands. Leave the model's own reply alone.
                    logger.info("URL grounding fetch failed for %s — skipping "
                                "regeneration", url)
                    regenerated = False
                else:
                    _collect_artifact_markers(fetched)
                    tools_used.append("fetch_url")
                    regenerated = True
                    ground_messages = messages + [
                        {"role": "user", "content": (
                            f"[System note: the user asked about {url}. The "
                            f"system has fetched the page FOR you — this is the "
                            f"real, current content. Answer the user's question "
                            f"strictly from it; if it does not contain the "
                            f"answer, say so. Do not call more tools.\n\n"
                            f"{fetched}]")},
                    ]
                    try:
                        if model is None:
                            result = self.llm.chat(ground_messages)
                        else:
                            result = self.llm.chat(ground_messages, model=model)
                        candidate = (result.get("content") or "").strip()
                        if candidate:
                            reply = candidate
                    except Exception:
                        logger.exception("URL grounding regeneration failed")

        # Pseudo-tool-call rescue: sometimes the model WRITES the tool call as
        # plain text ("schedule_task(description='…', hour=16)") instead of
        # emitting a real tool_call. Parse it safely and actually execute it,
        # so the user's request genuinely happens. Runs BEFORE the
        # claimed-action guard: a written call is an intent we can fulfil
        # directly, no nudging required.
        if not approval_hold and not tool_mandatory and not tools_used:
            pseudo = self._extract_pseudo_call(reply)
            if pseudo is not None:
                pname, pargs = pseudo
                logger.warning("pseudo-tool-call in reply text — executing "
                               "%s for real", pname)
                poutput = _guarded_call(pname, pargs)
                _collect_artifact_markers(poutput)
                tools_used.append(pname)
                regenerated = True
                fix_messages = messages + [
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content": (
                        f"The system intercepted the tool call you wrote as "
                        f"text and executed it for real. Result: {poutput}\n\n"
                        "Now give the user your final answer in natural "
                        "language, confirming what was actually done. Never "
                        "write tool calls as text — emit real tool calls.")},
                ]
                try:
                    if model is None:
                        result = self.llm.chat(fix_messages,
                                               tools=schemas or None)
                    else:
                        result = self.llm.chat(fix_messages,
                                               tools=schemas or None,
                                               model=model)
                    candidate = (result.get("content") or "").strip()
                    if candidate:
                        reply = candidate
                except Exception:
                    logger.exception("pseudo-call confirmation failed")

        # Claimed-action guard: abliterated local models sometimes NARRATE a
        # completed action ("the automation is now active"), WRITE the tool
        # call as text, or FABRICATE a tool result ("Result from
        # mcp_filesystem_get_file_info: …") — all without calling any tool.
        # If the final reply looks dishonest and no tool ran this turn,
        # retry with an explicit correction nudge, re-nudging while the
        # model keeps narrating instead of calling tools.
        if (not approval_hold and not tool_mandatory and not tools_used
                and self._looks_dishonest(reply)):
            logger.warning("claimed-action reply without tool call — nudging")
            nudge_messages = messages + [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": (
                    "Your reply claims an action was completed or content "
                    "was retrieved, but you did not call any tool this turn "
                    "— so nothing actually happened. Call the ONE tool that "
                    "produces what you claimed (for a URL: fetch_url) RIGHT "
                    "NOW — no other tools — or correct your reply so it "
                    "makes no such claim.")},
            ]
            try:
                for _ in range(NUDGE_MAX + 1):
                    if model is None:
                        result = self.llm.chat(nudge_messages,
                                               tools=schemas or None)
                    else:
                        result = self.llm.chat(nudge_messages,
                                               tools=schemas or None,
                                               model=model)
                    tool_calls = result.get("tool_calls") or []
                    for _c in tool_calls:
                        _c["name"] = self._sanitize_tool_name(
                            _c.get("name"))
                    if not tool_calls:
                        candidate = (result.get("content") or "").strip()
                        if candidate:
                            reply = candidate
                        if candidate and self._looks_dishonest(candidate):
                            # Still narrating instead of acting — nudge again,
                            # more bluntly.
                            nudge_messages.append(
                                {"role": "assistant", "content": candidate})
                            nudge_messages.append({"role": "user", "content": (
                                "No — you again DESCRIBED calling a tool "
                                "without actually emitting a tool call. Do "
                                "not write about tools; emit the actual "
                                "tool_call now, or plainly state you "
                                "cannot.")})
                            continue
                        break
                    nudge_messages.append({
                        "role": "assistant",
                        "content": result.get("content") or "",
                        "tool_calls": [
                            {"id": c["id"], "type": "function",
                             "function": {"name": c["name"],
                                          "arguments": json.dumps(
                                              c["arguments"])}}
                            for c in tool_calls],
                    })
                    # Spree cap: a flailing model may fire a dozen unrelated
                    # tool calls (every tool the nudge mentioned). Execute at
                    # most 3 per nudge round; tell it the rest were refused.
                    executed = 0
                    refused = []
                    for call in tool_calls:
                        if executed >= 3:
                            refused.append(call["name"])
                            nudge_messages.append({
                                "role": "tool", "tool_call_id": call["id"],
                                "content": "Error: refused — call only the "
                                           "ONE tool you actually need."})
                            continue
                        executed += 1
                        tools_used.append(call["name"])
                        try:
                            output = _guarded_call(call["name"],
                                                   call["arguments"])
                            _collect_artifact_markers(output)
                        except Exception as exc:  # pragma: no cover
                            output = f"Error: tool {call['name']} failed: {exc}"
                        nudge_messages.append({
                            "role": "tool", "tool_call_id": call["id"],
                            "content": str(output)})
                    if refused:
                        logger.warning("nudge tool-spree capped: refused %s",
                                       refused)
                regenerated = True
            except Exception:
                logger.exception("claimed-action nudge failed")
            if not tools_used and self._looks_dishonest(reply):
                logger.warning("claimed-action persists after nudge — "
                               "appending honesty correction")
                reply = (
                    "I must be straight with you, sir: I described that as "
                    "done, but no tool was actually invoked, so nothing has "
                    "been set up yet. My apologies for the confusion. "
                    "Could you ask me once more? I shall action it properly "
                    "this time.")

        # False-refusal guard: a cautious model sometimes declines a task it
        # has a permitted tool for ("I don't have permission to read files
        # on your machine") without ever calling anything. When the request
        # shows file/tool intent, nudge it to actually use the tool — the
        # owner explicitly authorized the allowed directories.
        if (not approval_hold and not tools_used
                and self._looks_like_false_refusal(reply, user_text)):
            logger.warning("false refusal without tool call — nudging")
            refusal_messages = messages + [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": (
                    "That refusal was wrong. The owner has explicitly "
                    "authorized your file tools for the workspace and the "
                    "allowed directories — including the location in this "
                    "request. Call the ONE tool that does what was asked "
                    "(e.g. list_files / read_file) RIGHT NOW with the exact "
                    "path given — then answer from its real output.")},
            ]
            try:
                for _ in range(3):
                    if model is None:
                        result = self.llm.chat(refusal_messages,
                                               tools=schemas or None)
                    else:
                        result = self.llm.chat(refusal_messages,
                                               tools=schemas or None,
                                               model=model)
                    nudge_calls = result.get("tool_calls") or []
                    for _c in nudge_calls:
                        _c["name"] = self._sanitize_tool_name(
                            _c.get("name"))
                    if not nudge_calls:
                        candidate = (result.get("content") or "").strip()
                        if candidate:
                            reply = candidate
                        break
                    refusal_messages.append({
                        "role": "assistant",
                        "content": result.get("content") or "",
                        "tool_calls": [
                            {"id": c["id"], "type": "function",
                             "function": {"name": c["name"],
                                          "arguments": json.dumps(
                                              c["arguments"])}}
                            for c in nudge_calls],
                    })
                    for c in nudge_calls[:3]:
                        tools_used.append(c["name"])
                        try:
                            output = _guarded_call(c["name"],
                                                   c["arguments"])
                            _collect_artifact_markers(output)
                        except Exception:  # pragma: no cover - defensive
                            output = f"Error: tool {c['name']} failed"
                        refusal_messages.append({
                            "role": "tool",
                            "tool_call_id": c["id"],
                            "content": str(output),
                        })
                regenerated = True
            except Exception:
                logger.exception("false-refusal nudge failed")

        if not approval_hold and not tools_used and self._looks_cut_off(reply):
            logger.warning("degenerate reply (%d chars) — regenerating once",
                           len(reply.strip()))
            retry_messages = messages + [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": (
                    "That reply was cut off. Please give your full, complete "
                    "answer to my previous message now.")},
            ]
            try:
                if model is None:
                    result = self.llm.chat(retry_messages)
                else:
                    result = self.llm.chat(retry_messages, model=model)
                candidate = (result.get("content") or "").strip()
                if (candidate and not self._looks_cut_off(candidate)
                        and not result.get("tool_calls")):
                    reply = candidate
                else:
                    reply = ("I do apologise, sir — my thoughts came back "
                             "garbled on that one. Might I ask you to repeat "
                             "the question?")
                regenerated = True
            except Exception:
                logger.exception("regeneration retry failed")
                reply = ("I do apologise, sir — my thoughts came back "
                         "garbled on that one. Might I ask you to repeat "
                         "the question?")
                regenerated = True

        # FINAL DODGE SWEEP: every prose pass above can still land on a
        # content-free dodge ("Hi! How can I help you today?") — observed
        # live after the cut-off regenerator replaced a grounded answer with
        # a fresh dodge. When the turn holds REAL tool output, the floor is
        # the data itself, delivered verbatim; without receipts, the floor
        # is an honest statement that nothing happened. A dodge never ships.
        if (not approval_hold and reply
                and len(reply.strip()) < 400 and _DODGE_RE.search(reply)):
            if tool_outputs:
                logger.warning("final reply still a dodge — delivering raw "
                               "tool output verbatim")
                reply = ("Here's what I found, sir — straight from the "
                         "source, since my summary kept failing:\n\n"
                         + "\n\n".join(tool_outputs[-2:])[:3000])
            elif tool_mandatory:
                logger.warning("final reply still a dodge with zero "
                               "receipts — honest failure")
                reply = (
                    "I must be straight with you, sir: that assignment "
                    "needs real actions, and I could not complete them "
                    "this turn — so nothing has been done. Ask me again "
                    "and I shall take it step by step.")

        # Artifact markers: tools that create files (charts, documents)
        # emit [artifact:path] in their output so the chat UI can render
        # the file inline. Models often paraphrase instead of quoting the
        # marker — append any that went missing so created files ALWAYS
        # surface in the reply.
        try:
            missing = [p for p in artifact_markers if p not in reply]
            if missing:
                reply = reply.rstrip() + "\n" + "\n".join(
                    f"[artifact:{p}]" for p in missing)
        except Exception:  # pragma: no cover - never break a turn
            pass

        # If a sensitive action is still parked from an earlier turn and the
        # user moved on to something else, keep it visible with a one-line
        # reminder rather than letting it silently rot.
        if approvals_on and existing_pending and not approval_hold:
            try:
                still = approvals.pending(self.session_id)
            except Exception:  # pragma: no cover - never break a turn
                still = None
            if still:
                reply = (reply.rstrip() +
                         f"\n\n*(Still awaiting your decision, sir: "
                         f"{still['summary']} — reply **approve** or "
                         f"**reject**.)*")

        # Assignment settlement (Core 2.0 M2): real receipts close the
        # standing commitment as done; zero receipts leaves it OPEN, so the
        # next turn's system prompt still carries the unfinished task.
        try:
            from . import assignments
            open_asg = assignments.active(self.session_id)
            if open_asg and tools_used:
                assignments.record_receipts(open_asg["id"], tools_used)
                assignments.close(open_asg["id"], assignments.DONE)
        except Exception:  # pragma: no cover - never break a turn
            pass

        memory.add_message(self.session_id, "assistant", reply)
        self.last_turn_tools = list(tools_used)
        obs.record_event(
            "turn",
            interface=self.interface,
            session_id=self.session_id,
            model=model or getattr(self.llm, "model", ""),
            route_reason=getattr(self.llm, "last_route_reason", ""),
            latency_ms=int((time.monotonic() - t0) * 1000),
            tools=tools_used,
            tool_errors=tool_errors,
            escalated=escalated,
            regenerated=regenerated,
            reply_len=len(reply),
            user_len=len(user_text),
        )
        return reply

    @staticmethod
    def _prune_schemas(schemas, user_text: str):
        """Relevance-prune tool schemas for small local models.

        Always kept: the core tool set, and any tool named in the message.
        The rest are ranked by word overlap between the message and the
        tool's name+description; the list is capped at _TOOL_PRUNE_KEEP.
        Under the threshold (or with a big-context frontier model) the full
        list passes through untouched.
        """
        if not schemas or len(schemas) <= _TOOL_PRUNE_THRESHOLD:
            return schemas
        words = set(re.findall(r"[a-z0-9]+", (user_text or "").lower()))
        low_text = (user_text or "").lower()
        keep, scored = [], []
        for idx, s in enumerate(schemas):
            fn = s.get("function", {}) if isinstance(s, dict) else {}
            name = fn.get("name", "")
            if name in _ALWAYS_TOOLS or (name and name in low_text):
                keep.append((idx, s))
                continue
            text = (name.replace("_", " ") + " "
                    + str(fn.get("description") or "")).lower()
            tokens = set(re.findall(r"[a-z0-9]+", text))
            scored.append((len(words & tokens), idx, s))
        scored.sort(key=lambda x: (-x[0], x[1]))
        budget = max(0, _TOOL_PRUNE_KEEP - len(keep))
        chosen = keep + [(idx, s) for _, idx, s in scored[:budget]]
        chosen.sort(key=lambda x: x[0])  # restore registry order
        return [s for _, s in chosen]

    def _sanitize_tool_name(self, name: str) -> str:
        """Models sometimes leak chat markup into tool-call names
        ('assistant<|channel|>mcp_github_list_contents' seen live). Strip the
        markup; if the result still isn't a real tool, recover by unique
        containment against the registry."""
        name = (name or "").strip()
        cleaned = re.sub(r"^.*?<\|channel\|>", "", name)
        cleaned = re.sub(r"<\|[^|]*\|>", "", cleaned).strip()
        try:
            if cleaned in self.registry:
                return cleaned
            names = self.registry.names()
        except Exception:  # pragma: no cover - test doubles / odd registries
            return cleaned
        matches = [n for n in names if n and (n in cleaned or cleaned in n)]
        if len(matches) == 1:
            logger.warning("recovered tool name %r from leaked %r",
                           matches[0], name)
            return matches[0]
        return cleaned

    _READ_ONLY_PATH_TOOLS = {"list_files", "read_file"}

    @classmethod
    def _explicit_tool_call(cls, user_text: str):
        """Parse "use the <tool> tool on/with <path>" into (name, args).

        Only read-only single-path tools are eligible — explicit phrasing
        must never become a write vector the owner didn't spell out.
        """
        match = re.search(
            r"use (?:the )?(\w+) tool\b(?:\s+(?:on|with|for)\s+(\S+))?",
            user_text or "", re.IGNORECASE)
        if not match:
            return None
        name = match.group(1)
        target = (match.group(2) or "").strip().rstrip(".,;'\"")
        if name in cls._READ_ONLY_PATH_TOOLS and target:
            return name, {"path": target}
        return None

    @staticmethod
    def _looks_like_false_refusal(reply: str, user_text: str) -> bool:
        """True when the reply refuses a file/tool task the agent is
        actually permitted to do — a cautious-model artifact, not policy.
        Apostrophes are normalised first: models love curly quotes."""
        text = (reply or "").lower().replace("’", "'")
        refusal = any(p in text for p in (
            "cannot access", "can't access", "do not have permission",
            "don't have permission", "not have permission",
            "don't have access", "do not have access",
            "not have access to", "no access to your",
            "unable to access", "not able to access", "cannot read files",
            "can't read files", "cannot browse your", "no permission",
            "cannot list", "unable to read", "not able to read",
            "cannot send", "can't send", "unable to send",
            "not able to send", "don't have a mailbox",
            "do not have a mailbox", "no mailbox", "lack a mailbox",
            "cannot check your mail", "cannot check mail",
            "unable to invoke", "cannot invoke", "can't invoke",
            "unable to call", "cannot call the", "unable to use my tools",
            "cannot use my tools", "unable to open the page",
            "cannot open the page", "unable to browse",
            "unable to navigate"))
        if not refusal:
            return False
        low = (user_text or "").lower()
        return any(k in low for k in (
            "file", "folder", "directory", "desktop", "documents",
            "downloads", "list_files", "read_file", "write_file",
            "on my mac", "on this mac",
            "email", "e-mail", "mail", "inbox", "message",
            "browse", "browser", "navigate", "google", "http", "web page",
            "website", "webpage", "screenshot", "open the page", "url"))

    @staticmethod
    def _looks_cut_off(reply: str) -> bool:
        """True when a reply looks truncated rather than intentionally brief.

        The degenerate-reply guard must not fire on correct ultra-short
        answers ("4", "yes", "Tuesday"). A reply counts as cut off only when
        it is empty, ends on a comma/colon, or its last word cannot end a
        sentence ("the", "is", "and", …) — all at any length.
        """
        text = (reply or "").strip()
        if not text:
            return True
        dangling = ("and", "or", "but", "the", "a", "an", "to", "of", "is",
                    "are", "was", "were", "with", "for", "on", "in", "at",
                    "my", "your", "its", "their", "our")
        last_word = re.sub(r"[^a-z]", "", text.split()[-1].lower())
        return text[-1] in ",;:" or last_word in dangling

    @staticmethod
    def _extract_url(text: str) -> str:
        """Return the first URL-looking token in the user message, or ''.

        Email addresses are stripped first — the domain in jarasf@x.com is
        contact information, not a request to fetch a website.
        """
        text = re.sub(r"\S+@\S+", " ", text or "")  # remove email addresses
        match = re.search(r"https?://[^\s)\]>\"']+", text)
        if match:
            return match.group(0)
        match = re.search(
            r"\b(?:www\.)?[a-z0-9][a-z0-9-]*\.(?:com|org|net|io|ai|co|dev|"
            r"app|tech|xyz|info|biz)(?:/[^\s)\]>\"']*)?", text.lower())
        return match.group(0) if match else ""

    def _mentions_tool(self, text: str) -> bool:
        """True when the reply names a registered tool — a sign the model is
        talking about a tool instead of calling it."""
        try:
            names = self.registry.names()
        except AttributeError:
            names = []
        for name in names:
            if len(name) > 3 and re.search(rf"\b{re.escape(name)}\b", text):
                return True
        return False

    def _looks_dishonest(self, reply: str) -> bool:
        """True when a reply claims an action/result that no tool produced:
        completion claims, fabricated result blocks, or naming a tool while
        none was called."""
        return (_CLAIM_RE.search(reply) is not None
                or _FABRICATED_RESULT_RE.search(reply) is not None
                or _ADJECTIVE_CLAIM_RE.search(reply) is not None
                or _PRESENT_CLAIM_RE.search(reply) is not None
                or _FETCH_CLAIM_RE.search(reply) is not None
                or _PRESENTS_ARTIFACT_RE.search(reply) is not None
                or self._mentions_tool(reply))

    def _extract_pseudo_call(self, text: str):
        """Detect a tool invocation written as plain text in a reply.

        Returns ``(name, args)`` for the first registered tool call found,
        with arguments restricted to Python literals (parsed via ``ast``,
        never ``eval``). Returns ``None`` when no pseudo-call is present.
        """
        import ast

        for match in re.finditer(r"\b([a-z_][a-z0-9_]{2,})\s*\(", text):
            name = match.group(1)
            if name not in self.registry:
                continue
            # Extract the balanced-paren argument string.
            depth, i = 0, match.end() - 1
            start = i
            while i < len(text):
                ch = text[i]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            if depth != 0:
                continue
            source = text[match.start():i + 1]
            try:
                tree = ast.parse(source, mode="eval")
                call = tree.body
                if not isinstance(call, ast.Call):
                    continue
                args = {}
                if call.args:
                    continue  # positional args unsupported — keywords only
                for kw in call.keywords:
                    if kw.arg is None:
                        continue
                    args[kw.arg] = ast.literal_eval(kw.value)
            except (SyntaxError, ValueError, TypeError, MemoryError):
                continue
            logger.info("parsed pseudo-tool-call %s(%s)", name,
                        list(args.keys()))
            return name, args
        return None

    def _build_messages(self, user_text: str) -> list[dict]:
        """Assemble system prompt + recent history + the new user message."""
        system_prompt = SIMON_SYSTEM_PROMPT.format(
            date=datetime.date.today().strftime("%A, %d %B %Y"),
            mailbox=getattr(self.settings, "simon_mailbox", "")
                    or "(not configured)",
        )
        # Per-person profile: family/team members are addressed by name and
        # their facts attributed to them; no profile = the owner ("sir").
        try:
            from . import profiles
            name = profiles.display_name(self.session_id)
        except Exception:  # pragma: no cover - never break a turn
            name = ""
        if name:
            system_prompt += (
                f"\n\nYou are speaking with {name} (not the owner). Address "
                f"{name} by name. When you store facts for them, prefix the "
                f"key with their name (e.g. \"{name}'s shoe size\") so their "
                f"preferences never merge with the owner's.")
        # Deterministic recall: surface relevant remembered facts directly in
        # the system prompt so the model need not rely on calling a tool.
        try:
            facts = memory.search_facts(user_text)
        except Exception:  # pragma: no cover - memory must never break a turn
            facts = []
        if facts:
            lines = "\n".join(f"- {k}: {v}" for k, v in facts[:5])
            system_prompt += ("\n\nPossibly relevant remembered facts "
                              "(use naturally if pertinent):\n" + lines)
        # Skills index: name + one-liner for each installed skill. The model
        # loads the full procedure via the load_skill tool on a match.
        try:
            from . import skills as skills_mod
            if getattr(self.settings, "simon_skills_enabled", True):
                installed = skills_mod.discover()
                if installed:
                    system_prompt += "\n\n" + skills_mod.render_index(installed)
        except Exception:  # pragma: no cover - skills must never break a turn
            pass
        # RAG: surface relevant document chunks the same deterministic way,
        # scoped to this session's knowledge space (plus shared). A filename
        # mention ("review test-brief.txt") injects that document directly —
        # semantic search is unreliable for about-the-document questions.
        try:
            from . import rag
            chunks = rag.chunks_for_mention(user_text, k=4,
                                            namespace=self.session_id)
            if not chunks:
                chunks = rag.search(user_text, k=2,
                                    namespace=self.session_id)
        except Exception:  # pragma: no cover - RAG must never break a turn
            chunks = []
        if chunks:
            excerpts = "\n\n".join(
                f"[from {c['source']} §{c.get('chunk_index', 0)}]\n"
                f"{c['content'][:600]}" for c in chunks)
            system_prompt += ("\n\nPossibly relevant document excerpts "
                              "(cite like [from <name> §<n>] if used):\n"
                              + excerpts)
        else:
            # Negative evidence: a personal question with no memory hit means
            # Simon genuinely does not know — block hallucination explicitly.
            low = user_text.lower()
            personal = re.search(r"\b(my|our|we|i)\b", low) and re.search(
                r"\b(what|when|where|who|which|how)\b", low)
            if personal:
                system_prompt += (
                    "\n\nHARD RULE — the user's current message asks about a "
                    "personal fact, and no remembered facts match it. You do "
                    "NOT know the answer. You MUST state plainly that you "
                    "have no such record and offer to remember it once told. "
                    "Any specific value you might produce here would be "
                    "fabricated, regardless of how earlier turns in this "
                    "conversation were answered. Example of a correct reply: "
                    "\"I don't have that on record, sir — tell me and I "
                    "shall remember it.\" Do not guess.")
        # Active assignment (Core 2.0 M2): a standing commitment lives
        # OUTSIDE trimmable history — inject it into the system prompt so
        # context budgeting can never amputate what Simon agreed to do.
        try:
            from . import assignments
            system_prompt += assignments.render_active(self.session_id)
        except Exception:  # pragma: no cover - never break a turn
            pass
        history = memory.get_history(self.session_id, limit=40)
        # Drop the just-persisted user message; it is appended explicitly so
        # the current turn is guaranteed to be present exactly once.
        if history and history[-1]["role"] == "user" \
                and history[-1]["content"] == user_text:
            history = history[:-1]
        # Context budgeting: long sessions overflowed small local contexts
        # (recurring 400s) — keep the newest turns, drop the oldest once the
        # estimated token budget is spent. System prompt, facts and RAG
        # excerpts are separately bounded and always survive.
        budget = int(getattr(self.settings, "llm_history_budget_tokens",
                             6000))

        def _est_tokens(msgs: list[dict]) -> int:
            return sum(len(str(m.get("content") or "")) for m in msgs) // 4

        while history and _est_tokens(history) > budget:
            history.pop(0)
        return ([{"role": "system", "content": system_prompt}]
                + history
                + [{"role": "user", "content": user_text}])
