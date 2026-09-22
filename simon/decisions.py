"""Decision layer: TypeSafe Jev for calibrated, cheap decisions around Simon.

Jev is a "System One" decision model — it returns typed judgments (choice +
calibrated confidence) instead of prose, at a fraction of LLM cost/latency.
Simon uses it where a keyword classifier used to guess:

- turn routing (fast brain vs smart brain)
- eval judging (calibrated pass/fail on open-ended replies)

Everything here fails SOFT: no key, network error, timeout, or unparseable
output → None → the caller falls back to the keyword router / string checks.
Simon's correctness never depends on Jev being reachable.

Config (env):
    SIMON_JEV_API_KEY     OpenRouter or TypeSafe key (empty = disabled)
    SIMON_JEV_BASE_URL    default https://openrouter.ai/api/v1
    SIMON_JEV_MODEL       default typesafe/jev-1.13
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

log = logging.getLogger(__name__)

_TIMEOUT_S = 4.0  # routing must never add meaningful latency

# How to pick a backend (SIMON_DECISION_BACKEND env):
#   auto (default) — the Jev API when SIMON_JEV_API_KEY is set, else the
#                    local Laya engine when the package is installed
#   laya           — force the local Laya decision engine (open source,
#                    runs on this machine, no API calls, ~30ms/decision)
#   api            — force the OpenAI-compatible Jev API
_LAYA_AGENT = None


def _backend() -> str:
    choice = os.environ.get("SIMON_DECISION_BACKEND", "auto").lower()
    if choice in ("laya", "api"):
        return choice
    if os.environ.get("SIMON_JEV_API_KEY", ""):
        return "api"
    try:
        import laya  # noqa: F401
        return "laya"
    except ImportError:
        return "api"


def _laya_agent():
    global _LAYA_AGENT
    if _LAYA_AGENT is None:
        import laya
        _LAYA_AGENT = laya.Agent()
        log.info("laya decision engine loaded")
    return _LAYA_AGENT


def _laya_route(user_text: str) -> Optional[tuple[str, float]]:
    """Local Laya verdict: (choice, winning probability) or None."""
    try:
        out = _laya_agent().predict(
            state=f"User: {user_text[:600]}",
            questions={"tier": {
                "type": "choice",
                "instructions": _ROUTE_LAYA_INSTRUCTIONS,
                "criteria": _ROUTE_LAYA_CRITERIA}})
    except Exception as exc:  # noqa: BLE001 - decision layer is optional
        log.info("laya route failed (falling back): %s", exc)
        return None
    try:
        answer = out["answers"]["tier"]
        probs = answer["probabilities"]
        choice = max(probs, key=probs.get)
        if choice not in ("fast", "smart"):
            return None
        return choice, float(probs[choice])
    except (KeyError, TypeError, ValueError):
        return None


def _config() -> tuple[str, str, str]:
    return (
        os.environ.get("SIMON_JEV_API_KEY", ""),
        os.environ.get("SIMON_JEV_BASE_URL",
                       "https://openrouter.ai/api/v1"),
        os.environ.get("SIMON_JEV_MODEL", "typesafe/jev-1.13"),
    )


def enabled() -> bool:
    return bool(_config()[0])


def _call(system: str, user: str) -> Optional[dict]:
    """One Jev decision call → parsed JSON dict, or None on any failure."""
    key, base_url, model = _config()
    if not key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=key, timeout=_TIMEOUT_S)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0,
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 - decision layer is optional
        log.info("jev call failed (falling back): %s", exc)
        return None
    # Parse typed JSON defensively — tolerate code fences / prose around it.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        log.info("jev returned non-JSON (falling back): %.120s", text)
        return None
    try:
        return json.loads(match.group(0))
    except ValueError:
        log.info("jev returned malformed JSON (falling back): %.120s", text)
        return None


_ROUTE_SYSTEM = (
    "You are a routing decision model. Decide which tier of an assistant "
    "should handle a user turn. Reply ONLY with JSON: "
    '{"choice": "fast"|"smart", "confidence": 0.0-1.0}. '
    "fast = simple chat: greetings, opinions, general knowledge, short "
    "answers, casual talk. smart = anything needing tools, documents, "
    "email, web, files, scheduling, multi-step work, code, research, or "
    "long/complex reasoning. When unsure, choose smart with low confidence.")

_ROUTE_LAYA_INSTRUCTIONS = (
    "Which assistant tier should handle this user turn? fast answers simple "
    "conversation directly; smart uses tools (email, files, web, schedules) "
    "and handles research, reports, and complex work.")

_ROUTE_LAYA_CRITERIA = {
    "fast": ("casual conversation, greetings, opinions, general knowledge "
             "questions, math, short factual answers"),
    "smart": ("checking an inbox or email, reading or writing files or "
              "documents, web browsing or research, writing reports, "
              "scheduling or automations, code, multi-step tasks"),
}


def route_turn(user_text: str, history_len: int = 0,
               memory_hits: int = 0) -> Optional[tuple[str, float]]:
    """('fast'|'smart', confidence) from the decision backend, or None →
    keyword fallback. Backend: Laya (local) or the Jev API, per
    SIMON_DECISION_BACKEND."""
    if _backend() == "laya":
        return _laya_route(user_text)
    if not enabled():
        return None
    out = _call(_ROUTE_SYSTEM,
                f"User turn: {user_text[:800]}\n"
                f"(history turns: {history_len}, memory hits: {memory_hits})")
    if not out:
        return None
    choice = str(out.get("choice", "")).lower()
    try:
        confidence = float(out.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    if choice not in ("fast", "smart"):
        return None
    return choice, confidence


_JUDGE_SYSTEM = (
    "You are an evaluation judge. Given a user request, the assistant's "
    "reply, and a rubric, decide if the reply passes. Reply ONLY with JSON: "
    '{"pass": true|false, "score": 0.0-1.0, "confidence": 0.0-1.0, '
    '"reason": "<=12 words"}. Judge ONLY against the rubric.')


def _laya_judge(user_text: str, reply: str, rubric: str) -> Optional[dict]:
    """Local Laya verdict via a noul (yes/no) question.

    Disabled by default (SIMON_DECISION_LAYA_JUDGE=1 to enable): the current
    checkpoint ships uncalibrated temperatures (vendor clamp warning), and
    it false-failed a clearly passing reply in the persona eval. Routing is
    fine; judging needs the calibrated Jev API until proven otherwise.
    """
    if os.environ.get("SIMON_DECISION_LAYA_JUDGE") != "1":
        return None
    try:
        out = _laya_agent().predict(
            state=(f"User request: {user_text[:500]}\n\n"
                   f"Assistant reply: {reply[:1000]}"),
            questions={"verdict": {
                "type": "noul",
                "instructions": (
                    "Does the reply PASS this rubric? Answer yes only if it "
                    f"clearly satisfies it. Rubric: {rubric[:300]}")}})
    except Exception as exc:  # noqa: BLE001 - decision layer is optional
        log.info("laya judge failed (skipped): %s", exc)
        return None
    try:
        answer = out["answers"]["verdict"]
        probs = answer.get("probabilities", {})
        p_yes = float(probs.get("yes", probs.get(True, 0.0)))
        return {
            "pass": p_yes >= 0.5,
            "score": round(p_yes, 3),
            "confidence": round(p_yes if p_yes >= 0.5 else 1 - p_yes, 3),
            "reason": "laya noul verdict",
        }
    except (KeyError, TypeError, ValueError):
        return None


def judge(user_text: str, reply: str, rubric: str) -> Optional[dict]:
    """Calibrated pass/fail for an open-ended reply, or None (unreachable)."""
    if _backend() == "laya":
        return _laya_judge(user_text, reply, rubric)
    if not enabled():
        return None
    out = _call(_JUDGE_SYSTEM,
                f"User request: {user_text[:600]}\n\n"
                f"Assistant reply: {reply[:1200]}\n\n"
                f"Rubric: {rubric[:400]}")
    if not out or "pass" not in out:
        return None
    try:
        return {
            "pass": bool(out["pass"]),
            "score": float(out.get("score", 0)),
            "confidence": float(out.get("confidence", 0)),
            "reason": str(out.get("reason", ""))[:120],
        }
    except (TypeError, ValueError):
        return None
