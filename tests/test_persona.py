"""Persona regression tests: the distilled honesty rules must stay in the
system prompt — they back the harness-level guards (claimed-action guard,
honesty intercept, spiral guard) at the model level."""

from simon.persona import SIMON_SYSTEM_PROMPT


def test_faithful_reporting_rules_present():
    prompt = SIMON_SYSTEM_PROMPT.lower()
    # Claims must rest on observed tool results.
    assert "tool result you actually saw" in prompt
    # Failures are reported first, never papered over.
    assert "say so first" in prompt
    # Partial work is never described as done.
    assert "partial work" in prompt
    assert "names what remains" in prompt


def test_memory_hygiene_rules_present():
    prompt = SIMON_SYSTEM_PROMPT.lower()
    # Update/dedupe rather than blindly filing duplicates.
    assert "rather than filing a duplicate" in prompt
    # Memory is for durable facts, not conversational trivia.
    assert "trivia" in prompt


def test_failed_tool_call_rule_present():
    prompt = SIMON_SYSTEM_PROMPT.lower()
    assert "never retry the" in prompt
    assert "identical call verbatim" in prompt
