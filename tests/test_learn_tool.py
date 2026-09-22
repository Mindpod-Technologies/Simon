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


# --- teach_skill (teach-by-demo lite) ----------------------------------------

def _teach_registry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = ToolRegistry()
    register_learn_tools(registry,
                         Settings(simon_workspace_dir=str(tmp_path / "ws")))
    return registry


def test_teach_skill_creates_discoverable_skill(tmp_path, monkeypatch):
    registry = _teach_registry(tmp_path, monkeypatch)
    out = registry.call("teach_skill", {
        "name": "Invoice Triage!",
        "description": "Sort incoming invoices by urgency and route them.",
        "procedure": "1. Fetch the inbox. 2. Flag anything past due. "
                     "3. Route vendor invoices to finance. 4. Summarize."})
    assert "learned and live" in out
    target = tmp_path / "data" / "skills" / "invoice-triage" / "SKILL.md"
    assert target.is_file()
    content = target.read_text()
    assert "name: invoice-triage" in content
    assert "1. Fetch the inbox." in content


def test_teach_skill_no_clobber_without_overwrite(tmp_path, monkeypatch):
    registry = _teach_registry(tmp_path, monkeypatch)
    args = {"name": "dup", "description": "A workflow for testing things.",
            "procedure": "1. First do this step carefully. 2. Then do that step. 3. Report the result to the owner."}
    assert "learned" in registry.call("teach_skill", args)
    out = registry.call("teach_skill", args)
    assert "already exists" in out
    out = registry.call("teach_skill", {**args, "overwrite": True})
    assert "learned and live" in out


def test_teach_skill_rejects_vague_input(tmp_path, monkeypatch):
    registry = _teach_registry(tmp_path, monkeypatch)
    assert "too short" in registry.call(
        "teach_skill", {"name": "x", "description": "short",
                        "procedure": "1. do it."})
    assert "proper name" in registry.call(
        "teach_skill", {"name": "!!!", "description": "desc here ok",
                        "procedure": "1. do it."})
