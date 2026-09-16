"""Capability overview: an instant, always-accurate answer to "what can
you do?" built from the LIVE tool registry instead of a model's guess.

Why this exists: small local models answer capability questions badly —
they hallucinate tool-call protocol talk ("I cannot emit a tool call…")
or invent features. The answer is knowable with certainty from the
registry, so we never ask a model.
"""

from __future__ import annotations

import re

# Clear capability questions. Deliberately strict: "can you do my taxes?"
# is a request, not a capability question, and must sail through; likewise
# "what can you do with this PDF" is a task, not a tour — excluded by the
# negative lookahead on with/to/for/about/on/using.
CAPABILITY_QUESTION_RE = re.compile(
    r"what\s+(?:\w+\s+){0,3}can\s+you\s+do\b"
    r"(?!\s+(?:with|to|for|about|on|using))"
    r"|what\s+you\s+can\s+do\b"
    r"(?!\s+(?:with|to|for|about|on|using))"
    r"|what\s+do\s+you\s+do\b"
    r"|what\s+(?:are|is)\s+your\s+(?:capabilities|abilities|features|skills)\b",
    re.IGNORECASE,
)

# Human-readable grouping by tool name. Anything unlisted falls under
# "and more" so new tools appear automatically.
_GROUPS = [
    ("Chat everywhere", {"chat"}, "Talk to me here, on Slack, Telegram, "
     "Teams, or by voice — one shared memory across all of them."),
    ("Remember", {"remember_fact", "recall_facts"},
     "Long-term memory: I store durable facts and recall them on any channel."),
    ("Act on the web", {"fetch_url", "web_search", "browser_goto"},
     "Fetch and read web pages, search the web, and drive a browser."),
    ("Work with files & documents", {"read_file", "write_file", "list_dir",
     "create_document", "upload_document"},
     "Read and write files in my workspace, review documents you upload, "
     "and draft new ones for download."),
    ("Charts & artifacts", {"create_chart", "list_artifacts"},
     "Turn numbers into charts rendered inline, and keep every file I make "
     "in the Artifacts panel."),
    ("Email & calendar", {"read_recent_emails", "send_email", "check_calendar"},
     "Read and send email from my own mailbox and keep track of commitments."),
    ("Automate", {"schedule_task", "list_schedules", "cancel_schedule",
     "set_reminder", "start_job"},
     "Schedule recurring automations, set reminders, and run big background "
     "jobs that report back when done."),
    ("Compute & system", {"calculate", "run_shell", "get_datetime"},
     "Calculate, check dates and times, and — where you allow it — run "
     "shell commands."),
    ("Grow a team", {"spawn_subagent", "delegate_dev"},
     "Spawn sub-agents for parallel work and hand coding tasks to dedicated "
     "developer agents."),
    ("Learn skills", {"load_skill", "create_skill"},
     "Follow packaged SKILL.md procedures — briefings, research, meeting "
     "notes — and learn new ones you drop in."),
]


def is_capability_question(text: str) -> bool:
    return bool(CAPABILITY_QUESTION_RE.search((text or "").strip()))


def capabilities_answer(schemas: list[dict] | None) -> str:
    """Build the overview from live tool schemas (grouped, butler-voiced)."""
    available = {s["function"]["name"] for s in (schemas or [])}
    lines = ["At your service, sir. A summary of what I can do:"]
    used: set[str] = set()
    for title, names, blurb in _GROUPS:
        if available & names:
            lines.append(f"- **{title}** — {blurb}")
            used |= available & names
    extra = sorted(available - used - {"chat"})
    if extra:
        lines.append(f"- Plus {len(extra)} more specialised tools "
                     f"({', '.join(extra[:6])}"
                     f"{', …' if len(extra) > 6 else ''}).")
    lines.append("Name a task and I shall get to it — or ask *what automations "
                 "exist* to see what's already running.")
    return "\n".join(lines)
