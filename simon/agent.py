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

    def _handle(self, user_text: str) -> str:
        """Handle one user turn and return Simon's final reply text."""
        memory.init_db()
        memory.add_message(self.session_id, "user", user_text)

        messages = self._build_messages(user_text)
        schemas = self.registry.schemas() if self.registry else None

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
        # truth, so the intercept must not deflect them.
        file_intent = any(k in low for k in (
            "file", "folder", "directory", "desktop", "document",
            "download", "spreadsheet", "pdf", "screenshot"))
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
            if decision == "approve":
                approvals.resolve(target["id"], "approved")
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
        failed_calls: dict[str, int] = {}
        artifact_markers: list[str] = []
        escalated = False
        approval_hold = False
        self.last_turn_exhausted = False
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

        fast_model = getattr(self.llm, "model_fast", None)
        for _ in range(0 if reply else MAX_ITERATIONS):
            # Fast tier handles simple turns without tools: sending the full
            # tool schemas (~18k tokens with MCP servers connected) would
            # dominate its latency. Tool-needing turns route smart via the
            # classifier, and mid-turn escalation restores full schemas.
            turn_tools = schemas or None
            if fast_model and model == fast_model:
                turn_tools = None
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
                            f"I shall do it at once, or **reject** and I'll "
                            f"stand down.")
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
            if not reply:
                reply = ("I do apologise, sir — I seem to have tied myself in "
                         "knots with tools. Perhaps we might try that again, "
                         "more simply?")
                self.last_turn_exhausted = True

        reply = reply or "Very good, sir. (No further response was required.)"

        # Deterministic URL grounding: when the user's message contains a URL
        # and no fetch-type tool ran this turn, do NOT rely on the model
        # choosing to fetch — fetch it ourselves and regenerate the reply
        # from the real content. The model summarises; the system guarantees
        # the grounding.
        regenerated = False
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
        if not approval_hold and not tools_used:
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
        if (not approval_hold and not tools_used
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

        memory.add_message(self.session_id, "assistant", reply)
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
            "cannot check your mail", "cannot check mail"))
        if not refusal:
            return False
        low = (user_text or "").lower()
        return any(k in low for k in (
            "file", "folder", "directory", "desktop", "documents",
            "downloads", "list_files", "read_file", "write_file",
            "on my mac", "on this mac",
            "email", "e-mail", "mail", "inbox", "message"))

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
        """Return the first URL-looking token in the user message, or ''."""
        match = re.search(r"https?://[^\s)\]>\"']+", text or "")
        if match:
            return match.group(0)
        match = re.search(
            r"\b(?:www\.)?[a-z0-9][a-z0-9-]*\.(?:com|org|net|io|ai|co|dev|"
            r"app|tech|xyz|info|biz)(?:/[^\s)\]>\"']*)?", (text or "").lower())
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
        # RAG: surface relevant document chunks the same deterministic way.
        # A filename mention ("review test-brief.txt") injects that document
        # directly — semantic search is unreliable for about-the-document
        # questions.
        try:
            from . import rag
            chunks = rag.chunks_for_mention(user_text, k=4)
            if not chunks:
                chunks = rag.search(user_text, k=2)
        except Exception:  # pragma: no cover - RAG must never break a turn
            chunks = []
        if chunks:
            excerpts = "\n\n".join(
                f"[from {c['source']}]\n{c['content'][:600]}" for c in chunks)
            system_prompt += ("\n\nPossibly relevant document excerpts "
                              "(cite naturally if used):\n" + excerpts)
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
        history = memory.get_history(self.session_id, limit=40)
        # Drop the just-persisted user message; it is appended explicitly so
        # the current turn is guaranteed to be present exactly once.
        if history and history[-1]["role"] == "user" \
                and history[-1]["content"] == user_text:
            history = history[:-1]
        return ([{"role": "system", "content": system_prompt}]
                + history
                + [{"role": "user", "content": user_text}])
