"""Agent loop: conversation management, tool dispatch, persona injection."""

from __future__ import annotations

import datetime
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

        # Deterministic honesty intercept: a personal question whose topic has
        # NEVER appeared in facts or conversation history gets a guaranteed
        # honest answer. Abliterated local models cannot be trusted not to
        # fabricate personal details (passwords, sizes, dates) — no prompt
        # rule is reliable against in-context priming.
        low = user_text.lower()
        personal = re.search(r"\b(my|our|we|i)\b", low) and re.search(
            r"\b(what|when|where|who|which|how)\b", low)
        if personal:
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

        reply = ""
        tool_errors = 0
        tools_used: list[str] = []
        failed_calls: dict[str, int] = {}
        escalated = False
        self.last_turn_exhausted = False
        t0 = time.monotonic()
        for _ in range(MAX_ITERATIONS):
            try:
                if model is None:
                    result = self.llm.chat(messages, tools=schemas or None)
                else:
                    result = self.llm.chat(messages, tools=schemas or None,
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
            # apologising to the user.
            frontier_model = getattr(self.llm, "model_frontier", "")
            if (getattr(self.llm, "frontier_enabled", False)
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
        if not _FETCH_TOOLS.intersection(tools_used):
            url = self._extract_url(user_text)
            if url and "fetch_url" in self.registry:
                logger.info("URL in user message but no fetch — grounding "
                            "deterministically via fetch_url(%s)", url)
                fetched = self.registry.call("fetch_url", {"url": url})
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
        if not tools_used:
            pseudo = self._extract_pseudo_call(reply)
            if pseudo is not None:
                pname, pargs = pseudo
                logger.warning("pseudo-tool-call in reply text — executing "
                               "%s for real", pname)
                poutput = self.registry.call(pname, pargs)
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
        if not tools_used and self._looks_dishonest(reply):
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
                            output = self.registry.call(call["name"],
                                                        call["arguments"])
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

        if not tools_used and self._looks_cut_off(reply):
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
            date=datetime.date.today().strftime("%A, %d %B %Y")
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
