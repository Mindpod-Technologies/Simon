"""Tests for simon.memory — facts + reminders CRUD against a temp database."""

import pytest

from simon import memory


@pytest.fixture()
def db(tmp_path):
    """A fresh, initialised temporary database path."""
    path = str(tmp_path / "simon.db")
    memory.init_db(path)
    return path


def test_init_db_creates_file_and_is_idempotent(db):
    memory.init_db(db)  # second call must not raise


def test_facts_roundtrip(db):
    memory.set_fact("favourite_drink", "Earl Grey", path=db)
    assert memory.get_fact("favourite_drink", path=db) == "Earl Grey"


def test_fact_upsert_overwrites(db):
    memory.set_fact("city", "London", path=db)
    memory.set_fact("city", "Edinburgh", path=db)
    assert memory.get_fact("city", path=db) == "Edinburgh"


def test_get_fact_missing_returns_none(db):
    assert memory.get_fact("no_such_key", path=db) is None


def test_search_facts_matches_key_and_value(db):
    memory.set_fact("favourite_drink", "Earl Grey", path=db)
    memory.set_fact("wife_birthday", "12 March", path=db)
    memory.set_fact("unrelated", "nothing", path=db)

    by_value = memory.search_facts("Earl", path=db)
    assert ("favourite_drink", "Earl Grey") in by_value

    by_key = memory.search_facts("birthday", path=db)
    assert by_key == [("wife_birthday", "12 March")]

    assert memory.search_facts("zzz-no-match", path=db) == []


def test_add_and_due_reminders(db):
    memory.add_reminder("future task", "2999-01-01T09:00:00", path=db)
    memory.add_reminder("past task", "2000-01-01T09:00:00", path=db)

    due = memory.due_reminders("2024-06-01T00:00:00", path=db)
    assert [r["text"] for r in due] == ["past task"]
    assert due[0]["run_at"] == "2000-01-01T09:00:00"
    assert isinstance(due[0]["id"], int)


def test_delete_reminder(db):
    rid = memory.add_reminder("temporary", "2000-01-01T00:00:00", path=db)
    assert memory.due_reminders("2024-01-01T00:00:00", path=db)
    memory.delete_reminder(rid, path=db)
    assert memory.due_reminders("2024-01-01T00:00:00", path=db) == []


def test_message_history_roundtrip(db):
    memory.add_message("s1", "user", "hello", path=db)
    memory.add_message("s1", "assistant", "Good evening, sir.", path=db)
    memory.add_message("s2", "user", "other session", path=db)

    history = memory.get_history("s1", path=db)
    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Good evening, sir."},
    ]


def test_history_limit_returns_most_recent(db):
    for i in range(50):
        memory.add_message("s", "user", f"msg {i}", path=db)
    history = memory.get_history("s", limit=40, path=db)
    assert len(history) == 40
    assert history[-1]["content"] == "msg 49"
    assert history[0]["content"] == "msg 10"
