"""Tests for the document workflow (simon/docs.py)."""

from __future__ import annotations

import pytest

from simon import docs
from simon.config import Settings


@pytest.fixture()
def settings(tmp_path):
    return Settings(simon_workspace_dir=str(tmp_path / "workspace"))


def test_safe_name_strips_paths_and_symbols():
    assert docs.safe_name("../../etc/passwd") == "passwd"
    assert docs.safe_name("my report: Q3?.md") == "my report_ Q3_.md"
    assert docs.safe_name("")  # never empty


def test_extract_text_plain(settings):
    p = settings.simon_workspace_dir
    import os
    os.makedirs(p, exist_ok=True)
    f = os.path.join(p, "note.txt")
    with open(f, "w") as fh:
        fh.write("hello world")
    assert docs.extract_text(f) == "hello world"


def test_extract_text_rejects_unknown_type(settings, tmp_path):
    bad = tmp_path / "data.bin"
    bad.write_bytes(b"\x00\x01\x02")
    with pytest.raises(ValueError, match="unsupported"):
        docs.extract_text(bad)


def test_save_upload_stores_and_extracts(settings):
    info = docs.save_upload(b"first para\n\nsecond para", "brief.txt",
                            settings, ingest=False)
    assert info["name"] == "brief.txt"
    assert info["chars"] == len("first para\n\nsecond para")
    assert info["chunks"] == 0
    uploads = docs.list_uploads(settings)
    assert [u["name"] for u in uploads] == ["brief.txt"]


def test_save_upload_rejects_empty(settings):
    with pytest.raises(ValueError, match="no extractable text"):
        docs.save_upload(b"   \n  ", "blank.txt", settings, ingest=False)


def test_save_upload_ingests_into_rag(settings, monkeypatch):
    captured = []
    import simon.rag as rag
    monkeypatch.setattr(rag, "add_document",
                        lambda path: captured.append(str(path)) or 3)
    info = docs.save_upload(b"some content here", "doc.md", settings,
                            ingest=True)
    assert info["chunks"] == 3
    assert captured and captured[0].endswith("doc.md.txt")


def test_create_document_md(settings):
    out = docs._create_document("Test Report", "Body text here", "md",
                                settings=settings)
    assert "Test_Report.md" in out
    path = docs.documents_dir(settings) / "Test_Report.md"
    assert path.read_text().startswith("# Test Report")
    assert docs.list_documents(settings)[0]["name"] == "Test_Report.md"


def test_create_document_docx(settings):
    out = docs._create_document("Board Pack", "Revenue is up.", "docx",
                                settings=settings)
    assert "Board_Pack.docx" in out
    path = docs.documents_dir(settings) / "Board_Pack.docx"
    assert path.stat().st_size > 0
    # Round-trip: the docx we wrote can be read back.
    text = docs.extract_text(path)
    assert "Revenue is up." in text


def test_create_document_validates(settings):
    assert "Error" in docs._create_document("", "x", "md", settings=settings)
    assert "Error" in docs._create_document("t", "", "md", settings=settings)
    assert "Error" in docs._create_document("t", "x", "pdf", settings=settings)


def test_resolve_document_blocks_traversal(settings):
    docs._create_document("real", "content", "md", settings=settings)
    assert docs.resolve_document(settings, "real.md") is not None
    assert docs.resolve_document(settings, "../.env") is None
    assert docs.resolve_document(settings, "missing.md") is None


def test_docs_tool_registers():
    from simon.tools import ToolRegistry
    registry = ToolRegistry()
    docs.register_docs_tools(registry, settings=None)
    assert "create_document" in registry
