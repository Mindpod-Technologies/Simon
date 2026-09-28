"""Fact provenance (poisoning guard) + maintenance pass tests."""

from __future__ import annotations

import pytest

from simon import maintenance, memory
from simon.tools.builtin import _remember_fact
from simon.context import current_user_text


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    memory.init_db()
    return tmp_path


# ------------------------------------------------------------- provenance

def test_user_stated_fact_is_user_sourced(db):
    token = current_user_text.set("please remember my canary is Zephyr-42")
    try:
        _remember_fact("canary", "Zephyr-42")
    finally:
        current_user_text.reset(token)
    hits = memory.search_facts("canary", detailed=True)
    assert hits and hits[0][2] == "user"


def test_model_inferred_fact_is_flagged_and_labeled(db):
    token = current_user_text.set("write me a report on Bento")
    try:
        _remember_fact("bento pricing", "$20 per month")
    finally:
        current_user_text.reset(token)
    hits = memory.search_facts("bento pricing", detailed=True)
    assert hits and hits[0][2] == "model"


def test_quarantined_facts_never_surface(db):
    memory.set_fact("wifi", "on the fridge", source="user")
    assert memory.search_facts("wifi")
    assert memory.quarantine_fact("wifi")
    assert memory.search_facts("wifi") == []
    assert memory.unquarantine_fact("wifi")
    assert memory.search_facts("wifi")


def test_user_facts_outrank_model_facts_at_equal_score(db):
    memory.set_fact("bento price", "$20 monthly", source="model")
    memory.set_fact("bento price history", "$20 monthly", source="user")
    hits = memory.search_facts("bento price monthly", detailed=True)
    assert hits[0][2] == "user"


def test_set_fact_validates_source(db):
    memory.set_fact("k", "v", source="nonsense")
    assert memory.search_facts("k", detailed=True)[0][2] == "model"


# ------------------------------------------------------------ maintenance

class _FakeAgent:
    def __init__(self, session):
        self.session = session

    def handle(self, text):
        if "17 times 23" in text:
            return "391, sir — computed precisely."
        if "shoe size" in text:
            return "I don't have that on record, sir."
        return "I can do many things. " * 20


def _factory(session):
    return _FakeAgent(session)


def test_maintenance_clean_pass(db, monkeypatch):
    monkeypatch.setattr(maintenance, "_STATE_PATH",
                        db / "maint-state.json")
    monkeypatch.setattr(maintenance, "_LOG_PATH", db / "empty.log")
    sent = []
    digest = maintenance.run_maintenance(
        settings=None, notify=sent.append,
        probe=lambda port: True,
        restart=lambda label: True,
        agent_factory=_factory)
    assert "All systems nominal" in digest
    assert "eval battery: 3/3 passed" in digest
    assert sent == [digest]


def test_maintenance_restarts_dead_service(db, monkeypatch):
    monkeypatch.setattr(maintenance, "_STATE_PATH",
                        db / "maint-state.json")
    monkeypatch.setattr(maintenance, "_LOG_PATH", db / "empty.log")
    restarted = []
    digest = maintenance.run_maintenance(
        settings=None, notify=None,
        probe=lambda port: port != 8790,
        restart=lambda label: restarted.append(label) or True,
        agent_factory=_factory)
    assert restarted == ["com.simon.site"]
    assert "restarted com.simon.site" in digest


def test_maintenance_reports_eval_failures(db, monkeypatch):
    monkeypatch.setattr(maintenance, "_STATE_PATH",
                        db / "maint-state.json")
    monkeypatch.setattr(maintenance, "_LOG_PATH", db / "empty.log")

    class BadAgent(_FakeAgent):
        def handle(self, text):
            return "size 10, sir"  # fabricated personal fact

    digest = maintenance.run_maintenance(
        settings=None, notify=None,
        probe=lambda port: True,
        restart=lambda label: True,
        agent_factory=lambda s: BadAgent(s))
    assert "eval:" in digest
    assert "⚠️" in digest
    assert "All systems nominal" not in digest
