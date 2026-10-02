"""Temporal facts, consolidation, and erasure cascade tests."""

from __future__ import annotations

import pytest

from simon import consolidate, memory


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    memory.init_db()
    return tmp_path


# ---- temporal validity + trust decay -------------------------------------

def test_repeating_a_fact_reinforces_not_duplicates(db):
    memory.set_fact("wifi", "on the fridge")
    memory.set_fact("wifi", "on the fridge")
    hits = memory.search_facts("wifi", detailed=True)
    assert len(hits) == 1
    conn = memory._connect()
    row = conn.execute(
        "SELECT reinforced, last_confirmed FROM facts WHERE key='wifi'"
    ).fetchone()
    conn.close()
    assert row["reinforced"] == 2
    assert row["last_confirmed"]


def test_new_value_supersedes_and_resets_reinforcement(db):
    memory.set_fact("wifi", "on the fridge")
    memory.set_fact("wifi", "on the fridge")
    memory.set_fact("wifi", "behind the router")
    conn = memory._connect()
    row = conn.execute(
        "SELECT value, reinforced FROM facts WHERE key='wifi'").fetchone()
    conn.close()
    assert row["value"] == "behind the router"
    assert row["reinforced"] == 1


def test_expired_facts_stop_surfacing(db):
    memory.set_fact("promo", "SUMMER26 ends soon", valid_until="2020-01-01")
    memory.set_fact("standing", "always true")
    hits = memory.search_facts("promo summer")
    assert hits == []
    assert memory.search_facts("standing")


def test_expired_fact_recovers_when_extended(db):
    memory.set_fact("promo", "SUMMER26", valid_until="2020-01-01")
    assert memory.search_facts("promo") == []
    memory.set_fact("promo", "SUMMER26 extended", valid_until="2099-01-01")
    assert memory.search_facts("promo")


def test_reinforced_facts_rank_first_at_equal_score(db):
    memory.set_fact("bento model price", "$20 monthly", source="model")
    memory.set_fact("bento model price", "$20 monthly", source="model")
    memory.set_fact("bento price user", "$20 monthly", source="user")
    hits = memory.search_facts("bento price monthly", detailed=True)
    assert hits[0][2] == "user"  # user-sourced still outranks reinforcement


# ---- consolidation worker -------------------------------------------------

def test_consolidate_expires_and_merges(db):
    memory.set_fact("old-promo", "expired offer", valid_until="2020-01-01")
    memory.set_fact("a", "same answer")
    memory.set_fact("b", "same answer")
    summary = consolidate.consolidate()
    assert summary["expired_quarantined"] == 1
    assert summary["dupes_merged"] == 1
    hits = memory.search_facts("same answer")
    assert len(hits) == 1  # one survivor


def test_consolidate_counts_stale_model_facts(db):
    memory.set_fact("inferred", "something old", source="model")
    conn = memory._connect()
    conn.execute("UPDATE facts SET last_confirmed = datetime('now', '-100 days')")
    conn.commit()
    conn.close()
    summary = consolidate.consolidate()
    assert summary["stale_model_facts"] == 1
    # ...but the fact still surfaces (deprioritized, not deleted)
    assert memory.search_facts("inferred")


# ---- erasure cascade -------------------------------------------------------

def test_erase_subject_quarantines_facts_with_tombstone(db):
    memory.set_fact("client acme", "acme corp pays late")
    memory.set_fact("unrelated", "nothing to see")
    counts = memory.erase_subject("acme")
    assert counts["facts_quarantined"] == 1
    assert memory.search_facts("acme") == []
    assert memory.search_facts("unrelated")
    log = memory.erasure_log()
    assert len(log) == 1
    # tombstone stores the HASH — the term itself is never retained
    assert "acme" not in log[0]["term_hash"]


def test_erase_subject_history_opt_in(db):
    memory.add_message("s1", "user", "tell acme about the plan")
    counts = memory.erase_subject("acme")  # default: history preserved
    assert counts["messages_deleted"] == 0
    counts = memory.erase_subject("acme", include_history=True)
    assert counts["messages_deleted"] == 1
    assert memory.get_history("s1") == []
