"""Core Tool abstraction for Simon's tool system."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Tool:
    """A callable tool exposed to the LLM.

    name:        unique tool name (e.g. "calculator")
    description: human/LLM-facing description of what the tool does
    parameters:  JSON-schema dict describing the arguments object
    func:        callable taking keyword args and returning a string result
    mutating:    True when the call changes state or is externally visible
                 (emails, posts, writes, schedules). Mutating tools REFUSE
                 unknown arguments instead of dropping them — a dropped
                 recipient/namespace/dry_run silently changes the action's
                 meaning (the "worst possible outcome" anti-pattern).
    idempotent:  True when re-running with identical args is always safe.
    """

    name: str
    description: str
    parameters: dict
    func: Callable[..., str]
    mutating: bool = False
    idempotent: bool = False
