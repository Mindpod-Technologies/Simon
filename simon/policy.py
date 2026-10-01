"""Policy pack: guardrails as data, not code.

The research consensus (Cedar/OPA/Cerbos, 2026): agent permissions belong
in a deterministic policy layer evaluated OUTSIDE the model, with verdicts
allow / deny / approve, deny-by-default for unknown risky actions, and the
base pack immutable per deployment (tenant overrides layer on top).

Simon starts embedded (no OPA server, no Rego): one YAML policy pack per
deployment — SIMON_POLICY_FILE overrides the bundled default. The approval
gate consults this FIRST; the hardcoded lists in approvals.py are the
fallback when no policy file exists. Multi-tenant path: a per-customer
pack path + tenant attributes in the rule `when` clauses later.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

_DEFAULT_PACK = Path(__file__).resolve().parent.parent / "policy.yml"
_cache: dict = {"path": None, "rules": None}


def _load(path: Optional[str] = None) -> list[dict]:
    pack_path = Path(path or os.environ.get("SIMON_POLICY_FILE", "")
                       or _DEFAULT_PACK)
    if _cache["path"] == str(pack_path) and _cache["rules"] is not None:
        return _cache["rules"]
    rules: list[dict] = []
    try:
        data = yaml.safe_load(pack_path.read_text())
        rules = list(data.get("rules") or [])
        log.info("policy pack loaded: %d rules from %s", len(rules),
                 pack_path.name)
    except FileNotFoundError:
        log.warning("policy pack %s not found — code defaults apply",
                    pack_path)
    except Exception:  # noqa: BLE001
        log.exception("policy pack failed to load — code defaults apply")
    _cache.update({"path": str(pack_path), "rules": rules})
    return rules


def reload() -> None:
    _cache.update({"path": None, "rules": None})


def _matches(rule_tool: str, name: str) -> bool:
    import fnmatch
    return fnmatch.fnmatchcase(name, rule_tool)


def evaluate(tool_name: str, args: dict) -> Optional[dict]:
    """First matching rule wins. Returns {"verdict", "reason"} or None.

    Verdicts: 'approve' (park for owner), 'deny' (blocked outright),
    'allow' (run). Rules may carry a 'when' dict of arg-regexes:
    {arg_name: "pattern"} — every listed pattern must match.
    """
    name = (tool_name or "").strip()
    args = args or {}
    for rule in _load():
        if not _matches(str(rule.get("tool", "")), name):
            continue
        when = rule.get("when") or {}
        matched = True
        for arg_key, pattern in when.items():
            if not re.search(str(pattern), str(args.get(arg_key, "")),
                             re.IGNORECASE):
                matched = False
                break
        if matched:
            reason = str(rule.get("reason", "policy rule"))
            # Reasons may template args: "send an email to {to}". Missing
            # keys stay literal — never crash a ruling.
            try:
                reason = reason.format_map(
                    _SafeArgs({k: str(v)[:80] for k, v in args.items()}))
            except Exception:  # noqa: BLE001
                pass
            return {"verdict": str(rule.get("verdict", "allow")),
                    "reason": reason}
    return None


class _SafeArgs(dict):
    def __missing__(self, key):
        return "{" + key + "}"
