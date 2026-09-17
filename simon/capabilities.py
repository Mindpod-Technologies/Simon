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
# negative lookahead on with/to/for/using. Note "on my behalf" IS a tour
# phrasing, so bare "on" is intentionally not excluded below.
CAPABILITY_QUESTION_RE = re.compile(
    r"what\s+(?:\w+\s+){0,3}can\s+you\s+do\b"
    r"(?!\s+(?:with|to|for|about|on|using))"
    r"|what\s+you\s+can\s+do\b"
    r"(?!\s+(?:with|to|for|about|on|using))"
    r"|what\s+do\s+you\s+do\b"
    r"|what\s+(?:are|is)\s+your\s+(?:capabilities|abilities|features|skills)\b"
    # "explain some automations that you can do (on my behalf)" — a tour
    # request in different clothes. The lookahead still protects tasks like
    # "tell me what you can do with this PDF".
    r"|(?:explain|describe|show|tell)\s+(?:me\s+)?(?:\w+\s+){0,5}"
    r"(?:you\s+can|can\s+you)\s+(?:do|run|handle|automate|perform|set\s+up|create|make)\b"
    r"(?!\s+(?:with|to|using))"
    r"|(?:what|which)\s+(?:automations?|things|tasks)\s+"
    r"(?:\w+\s+){0,3}(?:can|could)\s+you\s+(?:do|run|handle|automate|perform)\b"
    r"(?!\s+(?:with|to|using))"
    r"|how\s+(?:can|could)\s+you\s+help\b"
    # Paraphrase-sweep coverage (scripts/paraphrase battery + tests):
    r"|what\s+(?:are\s+you\s+able|do\s+you\s+know\s+how)\s+to\s+do\b"
    r"(?!\s+(?:with|to|using))"
    r"|what\s+can\s+you\s+help\b"
    r"|what\s+can\s+you\s+automate\b"
    r"|what\s+(?:are|is)\s+your\s+"
    r"(?:capabilities|abilities|features|skills|functions|tools)\b"
    r"|what\s+(?:features|functions|tools|skills|capabilities)\s+"
    r"do\s+you\s+(?:have|offer|support|include)\b"
    r"|(?:list|show\s+me|name)\s+your\s+"
    r"(?:capabilities|abilities|features|skills|functions|tools)\b"
    r"|(?:give\s+me\s+a\s+tour\s+of|walk\s+me\s+through|run\s+through)\s+"
    r"(?:your\s+)?(?:\w+\s+){0,3}"
    r"(?:features|capabilities|abilities|skills|functions|tools)\b"
    r"|tell\s+me\s+about\s+your\s+"
    r"(?:capabilities|abilities|features|skills|functions|tools)\b"
    r"|tell\s+me\s+what\s+you'?re\s+able\s+to\s+do\b"
    r"(?!\s+(?:with|to|using))"
    r"|(?:what\s+are\s+you|explain\s+what\s+you'?re)\s+capable\s+of\b"
    r"|(?:what|which)\s+(?:kinds?|sorts?|types?)\s+of\s+(?:\w+\s+){0,2}"
    r"(?:tasks?|things|work)\s+(?:\w+\s+){0,2}(?:can|could)\s+you\b"
    r"|how\s+(?:can|could)\s+you\s+(?:assist|be\s+of)\b",
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
