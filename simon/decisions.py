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


def route_turn(user_text: str, history_len: int = 0,
               memory_hits: int = 0) -> Optional[tuple[str, float]]:
    """('fast'|'smart', confidence) from Jev, or None → keyword fallback."""
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


def judge(user_text: str, reply: str, rubric: str) -> Optional[dict]:
    """Calibrated pass/fail for an open-ended reply, or None (unreachable)."""
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
