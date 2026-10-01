"""Trace spans: turn/chat/tool spans with token usage (OTel-lite)."""

from __future__ import annotations

import pytest

from simon import memory, obs


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH", str(tmp_path / "s.db"))
    memory.init_db()
    return tmp_path


def test_record_span_and_usage_rollup(db):
    obs.record_span("trace1", "turn", session_id="s1", duration_ms=1200)
    obs.record_span("trace1", "chat", parent="trace1", model="gpt-oss:20b",
                    duration_ms=900, prompt_tokens=500, completion_tokens=50)
    obs.record_span("trace1", "chat", parent="trace1", model="gpt-oss:20b",
                    duration_ms=800, prompt_tokens=400, completion_tokens=40)
    obs.record_span("trace1", "tool", parent="trace1", duration_ms=200,
                    detail="web_search")
    conn = memory._connect()
    rows = conn.execute("SELECT * FROM spans WHERE trace_id='trace1'").fetchall()
    conn.close()
    assert len(rows) == 4
    usage = obs.usage_by_day(days=1)
    assert usage[0]["model"] == "gpt-oss:20b"
    assert usage[0]["prompt_toks"] == 900 and usage[0]["completion_toks"] == 90
    assert usage[0]["calls"] == 2


def test_record_span_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "no" / "dir" / "x.db"))
    # _connect creates the dir — force failure by pointing at a file
    bad = tmp_path / "blocker"
    bad.write_text("not a dir")
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH", str(bad / "x.db"))
    obs.record_span("t", "chat")  # must not raise
