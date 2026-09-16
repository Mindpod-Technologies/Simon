"""Scheduler agent turns must run off the event loop, and the mail-check
prompt must not invite hallucinated web fetches.

Regression: the background mail check called agent.handle() synchronously,
so one slow tool call (a 20 s fetch_url timeout) stalled the whole event
loop — scheduler jobs missed their windows and the Slack socket dropped
every 5 minutes.
"""

import asyncio
import time

import pytest

from simon.config import Settings
from simon.scheduler import Scheduler


class FakeAgent:
    def __init__(self, reply="MAIL_CHECK_QUIET", delay=0.0):
        self.reply = reply
        self.delay = delay
        self.prompts = []

    def handle(self, prompt):
        self.prompts.append(prompt)
        if self.delay:
            time.sleep(self.delay)
        return self.reply


@pytest.fixture()
def idle(monkeypatch):
    """No interactive session active."""
    monkeypatch.setattr("simon.agent.interactive_session_active",
                        lambda: False)


def test_mail_check_prompt_forbids_web_fetches(idle):
    agent = FakeAgent()
    sent = []
    sched = Scheduler(Settings(), agent_factory=lambda: agent,
                      notify=sent.append)
    asyncio.run(sched._mail_check())
    assert len(agent.prompts) == 1
    prompt = agent.prompts[0]
    assert "read_recent_emails" in prompt
    assert "ONLY" in prompt
    assert "not a website" in prompt          # mailbox ≠ website, spelled out
    assert sent == []                          # quiet reply → no interruption


def test_mail_check_reports_new_mail(idle):
    agent = FakeAgent(reply="New invoice from Acme, sir.")
    sent = []
    sched = Scheduler(Settings(), agent_factory=lambda: agent,
                      notify=sent.append)
    asyncio.run(sched._mail_check())
    assert len(sent) == 1 and "Acme" in sent[0]


def test_mail_check_does_not_block_the_event_loop(idle):
    """A slow agent turn must leave the loop responsive."""
    agent = FakeAgent(delay=0.4)
    sched = Scheduler(Settings(), agent_factory=lambda: agent, notify=None)
    ticks = []

    async def main():
        async def ticker():
            for _ in range(100):
                ticks.append(1)
                await asyncio.sleep(0.05)
        await asyncio.gather(sched._mail_check(), ticker())

    asyncio.run(main())
    # If handle() ran on the loop, the 0.4 s sleep would starve the ticker
    # to ~1 tick; off-loop it should tick roughly 0.4/0.05 ≈ 8 times.
    assert len(ticks) >= 4


def test_mail_check_defers_during_interactive_session(monkeypatch):
    monkeypatch.setattr("simon.agent.interactive_session_active",
                        lambda: True)
    agent = FakeAgent()
    sched = Scheduler(Settings(), agent_factory=lambda: agent, notify=None)
    asyncio.run(sched._mail_check())
    assert agent.prompts == []                # skipped entirely
