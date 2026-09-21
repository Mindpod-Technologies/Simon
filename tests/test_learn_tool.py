"""Self-learning: ingest_note feeds Simon's own memory."""

from simon.config import Settings
from simon.tools import ToolRegistry
from simon.tools.learn_tool import register_learn_tools, _safe_name


def test_safe_name():
    assert _safe_name("Bento Digest 2026/09/21!") == "bento-digest-2026-09-21"
    assert _safe_name("") == "note"


def test_ingest_note_writes_and_indexes(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    captured = {}

    import simon.rag as rag
    monkeypatch.setattr(rag, "add_document",
                        lambda path, namespace="": captured.update(
                            path=str(path), ns=namespace) or 4)

    registry = ToolRegistry()
    register_learn_tools(registry,
                         Settings(simon_workspace_dir=str(tmp_path)))
    out = registry.call("ingest_note", {
        "name": "Bento Digest 2026-09-21",
        "text": "Bento organizes tasks by day with a focus on flow state."})
    assert "Ingested" in out and "4 chunks" in out
    dest = tmp_path / "notes" / "bento-digest-2026-09-21.md"
    assert dest.is_file()
    assert captured["path"] == str(dest)
    assert captured["ns"] == ""  # shared space


def test_ingest_note_rejects_thin_text(tmp_path):
    registry = ToolRegistry()
    register_learn_tools(registry,
                         Settings(simon_workspace_dir=str(tmp_path)))
    assert "too short" in registry.call("ingest_note",
                                        {"name": "x", "text": "tiny"})
