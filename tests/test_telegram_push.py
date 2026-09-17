"""Telegram progress push: thread-safe notify + /status remote control.

Regression guard for the silent-drop bug: JobRunner and sub-agents run in
worker threads with no event loop — notify() must still deliver, and the
owner must be able to ask /status from their phone.
"""

import asyncio
import threading

import pytest

from simon import approvals, jobs, schedules
from simon.config import Settings
from simon.interfaces import telegram_bot


class FakeBot:
    """Captures send_message calls; stands in for telegram.Bot."""

    sent: list = []

    def __init__(self, token=""):
        FakeBot.sent = []

    async def send_message(self, chat_id, text):
        FakeBot.sent.append((chat_id, text))


@pytest.fixture(autouse=True)
def _fake_bot(monkeypatch):
    monkeypatch.setattr("telegram.Bot", FakeBot)
    FakeBot.sent = []
    yield


@pytest.fixture()
def settings():
    return Settings(telegram_bot_token="fake-token",
                    telegram_allowed_user_ids="111,222")


def test_notify_from_worker_thread_delivers(settings):
    """The old bug: notify outside an event loop dropped the message."""
    notify = telegram_bot.make_notify(settings)
    done = threading.Event()

    def worker():
        notify("job finished, sir")
        done.set()

    t = threading.Thread(target=worker)
    t.start()
    assert done.wait(timeout=10)
    t.join(timeout=10)
    assert sorted(cid for cid, _ in FakeBot.sent) == [111, 222]
    assert all("job finished" in text for _, text in FakeBot.sent)


def test_notify_inside_event_loop_delivers(settings):
    notify = telegram_bot.make_notify(settings)

    async def main():
        notify("briefing, sir")
        await asyncio.sleep(0.2)  # let the create_task sends complete

    asyncio.run(main())
    assert sorted(cid for cid, _ in FakeBot.sent) == [111, 222]


def test_notify_truncates_and_ignores_empty(settings):
    notify = telegram_bot.make_notify(settings)
    notify("")
    assert FakeBot.sent == []
    notify("x" * 9000)
    assert all(len(text) <= 4000 for _, text in FakeBot.sent)


def test_approval_request_pushes_to_notifier(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    pushed = []
    approvals.set_notifier(pushed.append)
    try:
        approvals.request("job-7", "send_email", {"to": "a@b.c"},
                          "send an email to a@b.c", interface="job")
    finally:
        approvals.set_notifier(None)
    assert len(pushed) == 1
    assert "send an email" in pushed[0]
    assert "job" in pushed[0]


def test_status_text_covers_jobs_schedules_approvals(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    jobs.create_job("compile the weekly numbers", path=str(tmp_path / "simon.db"))
    schedules.add_schedule("pull last week's numbers", hour=9, minute=15,
                           day_of_week="fri", path=str(tmp_path / "simon.db"))
    approvals.request("web-abc", "send_email", {}, "send an email",
                      path=str(tmp_path / "simon.db"))

    text = telegram_bot.build_status_text()
    assert "status report" in text.lower()
    assert "weekly numbers" in text
    assert "fri at 09:15" in text
    assert "Awaiting your approval" in text
    assert "send an email" in text


def test_status_text_empty_system(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    text = telegram_bot.build_status_text()
    assert "none running or queued" in text
    assert "none scheduled" in text


# ---------------------------------------------------------------------------
# Owner-scoped recipients (the "bleed" regression guard)
# ---------------------------------------------------------------------------

def test_recipients_broadcast_when_unconfigured():
    s = Settings(telegram_bot_token="t", telegram_allowed_user_ids="111,222")
    assert telegram_bot.notify_recipients(s) == [111, 222]


def test_recipients_owner_from_identity_map():
    s = Settings(telegram_bot_token="t", telegram_allowed_user_ids="111,222",
                 simon_identity_map="slack:U1=owner,telegram:111=owner")
    assert telegram_bot.notify_recipients(s) == [111]


def test_recipients_explicit_owner_wins():
    s = Settings(telegram_bot_token="t", telegram_allowed_user_ids="111,222",
                 simon_identity_map="telegram:111=owner",
                 telegram_owner_user_id="222")
    assert telegram_bot.notify_recipients(s) == [222]


def test_notify_only_reaches_owner(monkeypatch):
    monkeypatch.setattr("telegram.Bot", FakeBot)
    FakeBot.sent = []
    s = Settings(telegram_bot_token="fake-token",
                 telegram_allowed_user_ids="111,222",
                 simon_identity_map="telegram:111=owner")
    telegram_bot.make_notify(s)("private operational notice")
    assert [cid for cid, _ in FakeBot.sent] == [111]
