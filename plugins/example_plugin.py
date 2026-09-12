"""Example Simon plugin — drop-in extension for the tool system.

HOW PLUGINS WORK
----------------
1. Put any ``*.py`` file in this ``plugins/`` directory (repo root).
2. Define a top-level ``register(registry)`` function.
3. Inside it, call ``registry.register(Tool(...))`` for each tool you add.
   A ``Tool`` needs: a unique ``name``, a ``description`` (the LLM reads this
   to decide when to use your tool), a ``parameters`` JSON-schema dict, and a
   ``func`` that takes keyword arguments and returns a string.
4. Restart Simon — the plugin is loaded automatically. A broken plugin is
   skipped with a logged warning and never breaks startup.

Tips:
- Keep funcs fast and side-effect-light; they run inside the agent loop.
- Always return a string (errors included) — never raise.
- Read settings via ``simon.config.get_settings()`` if you need config.
"""
from __future__ import annotations

import random

from simon.tools import Tool

_JOKES = [
    "I would tell you a UDP joke, but you might not get it.",
    "There are only 10 types of people: those who understand binary and those who don't.",
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "A SQL query walks into a bar, sees two tables, and asks... 'Mind if I JOIN you?'",
    "I told my computer I needed a break, and now it won't stop sending me KitKat ads.",
]


def _joke() -> str:
    return random.choice(_JOKES)


def register(registry) -> None:
    """Entry point called by Simon's plugin loader."""
    registry.register(Tool(
        name="joke",
        description="Tell a (mostly programming-related) joke.",
        parameters={"type": "object", "properties": {}, "required": []},
        func=_joke,
    ))
