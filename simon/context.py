"""Per-turn context: which session is currently being served.

Tools run deep inside the agent loop and don't receive the session as an
argument, but they need it to attribute created records (schedules, jobs,
reminders) to the person who asked — so notifications reach THEM, not just
the owner. The agent sets this context var at the start of every turn.
"""

from __future__ import annotations

import contextvars

current_session: contextvars.ContextVar[str] = contextvars.ContextVar(
    "simon_current_session", default="")
