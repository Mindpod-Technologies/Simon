"""Hourly-status activity digest: the status update must be grounded in the
real event log, never the model's imagination."""

import datetime

import pytest

from simon import memory, obs
from simon.scheduler import Scheduler
from simon.config import Settings


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    memory.init_db()
    return tmp_path


def _scheduler():
    return Scheduler(Settings(), agent_factory=None, notify=None)


def test_digest_reports_no_activity(db):
    digest = _scheduler()._activity_digest(hours=1.0)
    assert "No conversations" in digest
    assert "No background jobs" in digest


def test_digest_summarises_real_activity(db):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    obs.record_event("turn", interface="web", session_id="owner",
                     model="m", route_reason="r", latency_ms=1,
                     detail='{"tools": ["fetch_url"]}')
    obs.record_event("turn", interface="telegram", session_id="owner",
                     model="m", route_reason="r", latency_ms=1,
                     detail='{"tools": []}')
    # Eval sessions must be excluded.
    obs.record_event("turn", interface="eval", session_id="eval-xyz",
                     model="m", route_reason="r", latency_ms=1,
                     detail='{"tools": []}')
    digest = _scheduler()._activity_digest(hours=1.0)
    assert "web ×1" in digest
    assert "telegram ×1" in digest
    assert "eval" not in digest.split("Tools used")[0].split(":")[-1] \
        or "eval" not in digest
    assert "fetch_url ×1" in digest
