"""Approval gate: autonomous, not unsupervised.

Sensitive actions — anything irreversible or externally visible — pause and
wait for the owner to reply "approve" / "reject" before executing. Pending
requests live in SQLite so they survive restarts and work across every
interface (web, Slack, Teams, Telegram) that shares the session's agent.

Policy lives in ``assess()``: it returns a human summary when a tool call
needs approval, or None when the action is safe to run immediately.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from . import memory

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# A pending request older than this is treated as abandoned — the owner has
# moved on, so re-asking forever would be nagging.
PENDING_TTL_MINUTES = 60

# ---------------------------------------------------------------------------
# Policy: which tool calls need approval
# ---------------------------------------------------------------------------

_DESTRUCTIVE_SHELL_RE = re.compile(
    r"(\brm\b|\brmdir\b|\bunlink\b|git\s+push|git\s+reset\s+--hard"
    r"|\bkill\b|\bpkill\b|\bkillall\b|shutdown|reboot"
    r"|brew\s+(uninstall|remove)|pip\d*\s+uninstall|npm\s+uninstall"
    r"|chmod\s+-R|chown\s+-R|launchctl\s+(unload|bootout|remove)"
    r"|\bdd\b.*of=|\bmkfs\b|\bformat\b)",
    re.IGNORECASE)

# MCP tools are registered as mcp_<server>_<tool>; gate mutation verbs —
# anything that publishes, deletes, or moves money outside this machine.
_MCP_MUTATION_RE = re.compile(
    r"(push|merge|delete|remove|send|post|pay|charge|refund|transfer"
    r"|comment|invite|publish|deploy|destroy|terminate)",
    re.IGNORECASE)


def assess(tool_name: str, args: dict) -> Optional[str]:
    """Return a human summary if the call needs owner approval, else None."""
    name = (tool_name or "").strip()
    args = args or {}

    if name == "send_email":
        to = args.get("to", "?")
        subject = args.get("subject", "(no subject)")
        return f"send an email to {to} — \"{subject}\""
    if name == "delegate_dev":
        task = str(args.get("task", ""))[:120]
        return f"delegate development work to a coding agent — {task}"
    if name == "ha_call_service":
        return (f"call smart-home service {args.get('service', '?')} "
                f"on {args.get('entity_id', '?')}")
    if name == "run_shell":
        cmd = str(args.get("command", ""))
        if _DESTRUCTIVE_SHELL_RE.search(cmd):
            return f"run a destructive shell command — `{cmd[:100]}`"
        return None
    if name.startswith("mcp_") and _MCP_MUTATION_RE.search(name):
        detail = ""
        for key in ("title", "name", "path", "repo", "to", "message"):
            if args.get(key):
                detail = f" — {key}: {str(args[key])[:60]}"
                break
        return f"perform an external action via {name}{detail}"
    return None


# ---------------------------------------------------------------------------
# Reply classification
# ---------------------------------------------------------------------------

_APPROVE_RE = re.compile(
    r"^\s*(approve|approved|yes|yep|yeah|yup|ok|okay|sure|go ahead|do it"
    r"|proceed|confirm|confirmed|lgtm|ship it)\b[.!]?\s*$",
    re.IGNORECASE)
_REJECT_RE = re.compile(
    r"^\s*(reject|rejected|no|nope|nah|don'?t|cancel|stop|abort|never mind"
    r"|nevermind|hold off)\b[.!]?\s*$",
    re.IGNORECASE)
_DECISION_ID_RE = re.compile(
    r"^\s*(approve|reject)\s*#?(\d+)\s*[.!]?\s*$", re.IGNORECASE)


def classify_reply(text: str) -> Optional[str]:
    """'approve' / 'reject' / None for a user reply to a pending request."""
    if _APPROVE_RE.match(text or ""):
        return "approve"
    if _REJECT_RE.match(text or ""):
        return "reject"
    if _DECISION_ID_RE.match(text or ""):
        return _DECISION_ID_RE.match(text).group(1).lower()
    return None


def classify_decision(text: str) -> tuple[Optional[str], Optional[int]]:
    """Like classify_reply, but also parses an explicit id: 'approve 3'
    returns ('approve', 3). Used for cross-channel approval when several
    requests are pending at once."""
    m = _DECISION_ID_RE.match(text or "")
    if m:
        return m.group(1).lower(), int(m.group(2))
    return classify_reply(text), None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

# Optional push hook (Telegram notify) so approval asks raised by background
# contexts — jobs, scheduled automations — actually reach the owner's pocket
# instead of sitting silently in a session nobody is watching.
# _NOTIFIER(text) → owner channel (legacy); _SESSION_NOTIFIER(session, text)
# → the person who triggered the ask (preferred when set).
_NOTIFIER = None
_SESSION_NOTIFIER = None


def set_notifier(fn) -> None:
    """Register a notify(text) callback fired when an approval is requested."""
    global _NOTIFIER
    _NOTIFIER = fn


def set_session_notifier(fn) -> None:
    """Register a notify(session_id, text) callback — per-person delivery."""
    global _SESSION_NOTIFIER
    _SESSION_NOTIFIER = fn


def list_pending(path: Optional[str] = None) -> list[dict]:
    """All live pending requests, newest first (any session)."""
    init_db(path)
    conn = memory._connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE status = 'pending'"
            " AND created_at >= datetime('now', ?)"
            " ORDER BY id DESC",
            (f"-{PENDING_TTL_MINUTES} minutes",)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def init_db(path: Optional[str] = None) -> None:
    conn = memory._connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def request(session_id: str, tool: str, args: dict, summary: str,
            interface: str = "", path: Optional[str] = None) -> int:
    """Store a pending approval request, push it to the owner, return its id."""
    init_db(path)
    # One pending request per session at a time — a newer request supersedes.
    conn = memory._connect(path)
    try:
        conn.execute(
            "UPDATE approvals SET status = 'rejected' "
            "WHERE session_id = ? AND status = 'pending'", (session_id,))
        cur = conn.execute(
            "INSERT INTO approvals (session_id, tool, args_json, summary)"
            " VALUES (?, ?, ?, ?)",
            (session_id, tool, json.dumps(args or {}), summary))
        conn.commit()
        approval_id = int(cur.lastrowid)
    finally:
        conn.close()
    if _SESSION_NOTIFIER is not None or _NOTIFIER is not None:
        try:
            where = interface or session_id
            text = (
                f"Approval needed (via {where}): I'd like to "
                f"{summary}. Reply **approve** in any conversation with me "
                f"— or **approve #{approval_id}** if more than one matter "
                f"is pending.")
            if _SESSION_NOTIFIER is not None:
                _SESSION_NOTIFIER(session_id, text)
            else:
                _NOTIFIER(text)
        except Exception:  # noqa: BLE001 - a failed push must not lose the ask
            logger.exception("approval notifier failed")
    return approval_id


def pending(session_id: str, path: Optional[str] = None) -> Optional[dict]:
    """The session's live pending request, or None (expired/none)."""
    init_db(path)
    conn = memory._connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM approvals WHERE session_id = ? AND status = 'pending'"
            " AND created_at >= datetime('now', ?)"
            " ORDER BY id DESC LIMIT 1",
            (session_id, f"-{PENDING_TTL_MINUTES} minutes")).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def resolve(approval_id: int, status: str, path: Optional[str] = None) -> None:
    init_db(path)
    conn = memory._connect(path)
    try:
        conn.execute("UPDATE approvals SET status = ? WHERE id = ?",
                     (status, int(approval_id)))
        conn.commit()
    finally:
        conn.close()


def decode_args(row: dict) -> dict:
    try:
        return json.loads(row.get("args_json") or "{}")
    except (TypeError, ValueError):
        return {}
