"""OpenAI-compatible chat client with tool-call support.

Works with any OpenAI-compatible endpoint (OpenAI, Anthropic via base_url,
Ollama, OpenRouter, ...) through the official ``openai`` SDK.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from openai import OpenAI

log = logging.getLogger(__name__)

# --- Router heuristics ------------------------------------------------------
# Explicit user overrides that force the smart model for the turn.
SMART_OVERRIDE_PHRASES = (
    "think harder", "think deeply", "deep dive", "deep think",
    "use the big brain", "be thorough", "full analysis",
)

# Explicit user overrides that force the frontier (cloud) model for the turn.
FRONTIER_OVERRIDE_PHRASES = (
    "use kimi", "ask kimi", "kimi k3", "use the frontier",
    "frontier model", "use frontier", "biggest brain",
)

# Task shapes that benefit from the larger model.
SMART_KEYWORDS = (
    "compare", "versus", " vs ", "pros and cons", "research", "analyse",
    "analyze", "write code", "write a script", "debug", "refactor",
    "architecture", "design a", "plan a", "essay", "report", "draft a",
    "explain in detail", "step by step", "summarize this", "summarise this",
    "review this", "translate", "why does", "how does", "strategy",
    "calculate", "estimate the", "evaluate",
    # Memory operations: the fast model acknowledges but forgets to call the
    # tool — route these to the smart model which follows through.
    "remember", "remind me", "note that", "don't forget", "do not forget",
    "keep in mind", "commit this", "save this",
    # Mail: the fast tier runs tool-less, so any mail request routed there
    # produces the false "I cannot send email" refusal.
    "email", "e-mail", "mail", "inbox",
    # Corrections and meta-feedback: the fast model tends to paraphrase its
    # previous reply instead of engaging with the correction.
    "you said this", "you already said", "not what i asked", "try again",
    "that's not what", "that is not what", "you repeated", "wrong answer",
    # Recurring automations: scheduling requests must call schedule_task;
    # the smart model's tool discipline is materially better.
    "every morning", "every day", "each week", "every week", "each monday",
    "every monday", "every friday", "recurring", "automate", "automation",
    # Explicit MCP tool requests: naming an external tool means the user
    # expects a real call, which needs the smart model's tool discipline.
    "mcp_",
    # Connected integrations: any turn referencing a wired-up external service
    # expects a tool call. The fine-tuned fast model (mindpod-qwen3) answers
    # from prose knowledge instead of calling tools — 2026-09-14 regression
    # found when "list my GitHub repos" silently no-called on the fast tier.
    "github", "repository", "repositories", "pull request", "merge request",
    "open an issue", "file an issue", "create a branch", "push a commit",
    "clone the", "fork the",
    # Local file operations: the fast tier runs tool-less, so any turn about
    # the owner's folders/files must route to the smart model's tools.
    "desktop", "downloads", "documents folder", "my documents", "folder",
    "directory", "list files", "on my mac", "on this mac",
)


def classify_turn(user_text: str, history_len: int,
                  memory_hits: int) -> tuple[bool, str]:
    """Decide whether a turn deserves the smart model.

    Returns ``(use_smart, reason)``. Pure heuristics — zero added latency.
    """
    text = (user_text or "").strip()
    low = f" {text.lower()} "

    for phrase in SMART_OVERRIDE_PHRASES:
        if phrase in low:
            return True, f"explicit override ('{phrase.strip()}')"

    if len(text) > 280:
        return True, f"long message ({len(text)} chars)"

    if ("http://" in low or "https://" in low
            or re.search(r"\bwww\.[a-z0-9-]+\.[a-z]{2,}\b", low)
            or re.search(
                r"\b[a-z0-9][a-z0-9-]*\.(com|org|net|io|ai|co|dev|app|"
                r"tech|xyz|info|biz)\b", low)):
        return True, "contains a URL to analyse"

    if low.count("?") >= 2:
        return True, "multi-part question"

    for kw in SMART_KEYWORDS:
        if kw in low:
            return True, f"complex-task keyword ('{kw.strip()}')"

    # Skill-shaped requests: naming an installed skill means a multi-step
    # tool procedure is expected — the fast model acknowledges these without
    # calling load_skill; the smart model follows through.
    try:
        from . import skills as skills_mod
        for skill in skills_mod.discover():
            if skill.name in low or skill.name.replace("-", " ") in low:
                return True, f"matches skill '{skill.name}'"
    except Exception:  # noqa: BLE001 - skills must never break routing
        pass

    # Declarative personal-fact statements ("my email is …", "your address
    # is …") carry durable facts but no memory keyword. The fast model
    # acknowledges these without calling remember_fact — route to the smart
    # model, which follows through on the persona's storage discipline.
    # Excludes question-bearing messages: those must route by answering
    # intent, not storage intent.
    questionish = re.search(r"\b(what|which|how|why|when|where|who)\b", low)
    if (not questionish and "?" not in text
            and re.search(r"\b(my|our|your)\b", low)
            and re.search(
                r"\b(is|are|was|were|lives|works|belongs|located)\b", low)):
        return True, "declarative personal-fact statement"

    # Personal/factual question with nothing in long-term memory → the small
    # model would just guess; send it to the smart one.
    personal_q = re.search(r"\b(my|our|we|i)\b", low) and re.search(
        r"\b(what|when|where|who|which|how)\b", low)
    if personal_q and memory_hits == 0:
        return True, "personal/factual question with no memory hit"

    return False, "simple turn"


class LLM:
    """Thin wrapper around an OpenAI-compatible chat completions endpoint."""

    def __init__(self, settings: Any) -> None:
        """Create a client from settings (llm_base_url / llm_api_key / llm_model)."""
        self.model: str = settings.llm_model
        self._client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key or "simon-no-key",
        )
        # Ollama: skip hidden chain-of-thought ("thinking") for responsiveness
        # and sample conservatively — a factual assistant should not invent.
        # Harmless to remove if the endpoint is not Ollama.
        self._extra: dict[str, Any] = {}
        if "11434" in (settings.llm_base_url or ""):
            self._extra["extra_body"] = {"reasoning_effort": "none",
                                         "temperature": 0.4,
                                         # Keep the model resident: reloads
                                         # cost 5-15 s on a single-GPU box.
                                         "keep_alive": "24h"}

        # Model router: fast model for simple turns, llm_model for the rest.
        self.model_fast: str = settings.llm_model_fast or self.model
        self.router_enabled: bool = bool(
            settings.llm_router_enabled
            and settings.llm_model_fast
            and settings.llm_model_fast != settings.llm_model)
        self.last_route_reason: str = ""

        # Frontier tier (optional cloud model, e.g. Kimi K3): only active
        # when an API key is configured. Used for explicit user requests and
        # as the escalation of last resort when the local tiers fail.
        self.model_frontier: str = getattr(
            settings, "llm_frontier_model", "") or ""
        frontier_key = getattr(settings, "llm_frontier_api_key", "") or ""
        self.frontier_enabled: bool = bool(frontier_key and
                                           self.model_frontier)
        self._frontier_client: Optional[OpenAI] = None
        self._frontier_extra: dict[str, Any] = {}
        if self.frontier_enabled:
            self._frontier_client = OpenAI(
                base_url=getattr(settings, "llm_frontier_base_url", "")
                or "https://api.moonshot.ai/v1",
                api_key=frontier_key,
            )
            effort = getattr(
                settings, "llm_frontier_reasoning_effort", "") or ""
            if effort:
                self._frontier_extra["extra_body"] = {
                    "reasoning_effort": effort}

    def route_model(self, user_text: str, history_len: int,
                    memory_hits: int) -> str:
        """Return the model to use for a turn (router; off → default model)."""
        if not self.router_enabled:
            self.last_route_reason = "router disabled"
            return self.model
        use_smart, reason = classify_turn(user_text, history_len, memory_hits)
        model = self.model if use_smart else self.model_fast
        self.last_route_reason = reason
        log.info("router: '%.40s…' → %s (%s)", user_text, model, reason)
        return model

    def wants_frontier(self, user_text: str) -> bool:
        """True when the user explicitly asks for the frontier model."""
        low = f" {(user_text or '').lower()} "
        return any(p in low for p in FRONTIER_OVERRIDE_PHRASES)

    def chat(self, messages: list[dict],
             tools: Optional[list[dict]] = None,
             model: Optional[str] = None) -> dict:
        """Send a chat completion request.

        Returns ``{"content": str | None,
        "tool_calls": [{"id", "name", "arguments": dict}]}``. Robust to
        empty/odd responses: missing content becomes ``None`` and malformed
        tool-call arguments fall back to an empty dict.

        When ``model`` names the frontier model and a frontier key is
        configured, the request goes to the frontier endpoint; Ollama-specific
        sampling extras are not applied there.
        """
        model = model or self.model
        if (self.frontier_enabled and self._frontier_client is not None
                and model == self.model_frontier):
            kwargs: dict[str, Any] = {"model": model, "messages": messages}
            kwargs.update(self._frontier_extra)
            if tools:
                kwargs["tools"] = tools
            response = self._frontier_client.chat.completions.create(**kwargs)
            self._log_usage(model, response)
            if not response.choices:
                return {"content": None, "tool_calls": []}
            message = response.choices[0].message
            return {
                "content": message.content or None,
                "tool_calls": self._normalize_tool_calls(message),
            }

        kwargs = {"model": model, "messages": messages}
        kwargs.update(self._extra)
        if tools:
            kwargs["tools"] = tools
        response = self._client.chat.completions.create(**kwargs)
        self._log_usage(model, response)

        if not response.choices:
            return {"content": None, "tool_calls": []}

        message = response.choices[0].message
        return {
            "content": message.content or None,
            "tool_calls": self._normalize_tool_calls(message),
        }

    @staticmethod
    def _log_usage(model: str, response: Any) -> None:
        """Log prompt/completion token counts when the endpoint reports
        them — makes latency diagnosis possible from logs alone."""
        usage = getattr(response, "usage", None)
        if usage is not None:
            log.info("llm %s: %s prompt + %s completion tokens", model,
                     getattr(usage, "prompt_tokens", "?"),
                     getattr(usage, "completion_tokens", "?"))

    @staticmethod
    def _normalize_tool_calls(message: Any) -> list[dict]:
        """Normalize SDK tool-call objects into plain dicts."""
        normalized: list[dict] = []
        for index, call in enumerate(getattr(message, "tool_calls", None) or []):
            function = getattr(call, "function", None)
            raw_arguments = getattr(function, "arguments", "") if function else ""
            try:
                arguments = json.loads(raw_arguments) if raw_arguments else {}
                if not isinstance(arguments, dict):
                    arguments = {}
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            normalized.append({
                "id": getattr(call, "id", None) or f"call_{index}",
                "name": getattr(function, "name", "") if function else "",
                "arguments": arguments,
            })
        return normalized
