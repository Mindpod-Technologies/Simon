"""Per-turn context: which session is currently being served.

Tools run deep inside the agent loop and don't receive the session as an
argument, but they need it to attribute created records (schedules, jobs,
reminders) to the person who asked — so notifications reach THEM, not just
the owner. The agent sets this context var at the start of every turn.

``current_user_text`` carries the raw user message for provenance: a fact
written by the model is only "user-sourced" when the human actually stated
it in this message — otherwise the model inferred it, and inferred facts
are labeled unverified at recall time.
"""

from __future__ import annotations

import contextvars

current_session: contextvars.ContextVar[str] = contextvars.ContextVar(
    "simon_current_session", default="")

current_user_text: contextvars.ContextVar[str] = contextvars.ContextVar(
    "simon_current_user_text", default="")
