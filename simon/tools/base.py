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
    """

    name: str
    description: str
    parameters: dict
    func: Callable[..., str]
