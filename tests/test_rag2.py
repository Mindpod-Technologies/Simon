"""RAG 2.0: scoped knowledge spaces + hybrid retrieval + citation fields."""

import pytest

from simon import rag


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    # Deterministic fake embeddings: vector similarity is not under test —
    # scoping and fusion are. Encode the text's first letter as the vector.
    monkeypatch.setattr(rag, "embed",
                        lambda text: [float(ord((text or " ")[0].lower())), 1.0])
    return str(tmp_path / "simon.db")


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_namespaced_ingest_and_scoped_search(db, tmp_path):
    rag.add_document(_write(tmp_path, "owner-plan.txt",
                            "The owner plan is Zephyr Nine."),
                     namespace="owner")
    rag.add_document(_write(tmp_path, "wife-list.txt",
                            "Alicia's reading list includes Dune."),
                     namespace="8959938993")
    rag.add_document(_write(tmp_path, "shared-faq.txt",
                            "The shared wifi password is hunter2."),
                     namespace="")

    # The wife sees her doc + shared, NOT the owner's. (Fake embeddings make
    # every doc "similar", so what matters is EXCLUSION of other spaces.)
    hits = rag.search("Zephyr Nine owner plan", k=5, min_score=0.0,
                      namespace="8959938993")
    sources = {h["source"] for h in hits}
    assert "owner-plan.txt" not in sources
    assert sources <= {"wife-list.txt", "shared-faq.txt"}
    hits = rag.search("wifi password", k=5, min_score=0.0,
                      namespace="8959938993")
    assert "shared-faq.txt" in {h["source"] for h in hits}
    assert "owner-plan.txt" not in {h["source"] for h in hits}

    # The owner sees own + shared, NOT the wife's.
    hits = rag.search("reading list Dune", k=5, min_score=0.0,
                      namespace="owner")
    assert "wife-list.txt" not in {h["source"] for h in hits}
    assert "owner-plan.txt" in {h["source"] for h in hits}

    # Unscoped (legacy) search sees everything.
    hits = rag.search("Dune reading list", k=5, min_score=0.0)
    assert "wife-list.txt" in {h["source"] for h in hits}


def test_mention_respects_namespace(db, tmp_path):
    rag.add_document(_write(tmp_path, "secret-brief.txt",
                            "Top secret content here."), namespace="owner")
    hits = rag.chunks_for_mention("what does secret-brief.txt say?",
                                  namespace="8959938993")
    assert hits == []
    hits = rag.chunks_for_mention("what does secret-brief.txt say?",
                                  namespace="owner")
    assert hits and hits[0]["source"] == "secret-brief.txt"
    assert "chunk_index" in hits[0]


def test_fts_keyword_hits_exact_strings(db, tmp_path):
    """Embeddings are fuzzy; FTS must land exact tokens the vector misses."""
    rag.add_document(
        _write(tmp_path, "errors.txt",
               "Incident INC-2094: timeout in the payments capture step.\n\n"
               "Root cause: expired TLS certificate."),
        namespace="owner")
    hits = rag.search("what is INC-2094", k=3, min_score=0.99,
                      namespace="owner")
    assert hits and hits[0]["source"] == "errors.txt"
    assert "INC-2094" in hits[0]["content"]


def test_results_carry_chunk_index_for_citations(db, tmp_path):
    rag.add_document(_write(tmp_path, "report.md",
                            "First chunk.\n\nSecond chunk with details."),
                     namespace="owner")
    hits = rag.chunks_for_mention("report.md summary", namespace="owner")
    assert hits and all("chunk_index" in h for h in hits)


def test_reingest_replaces_only_same_namespace(db, tmp_path):
    rag.add_document(_write(tmp_path, "doc.txt", "shared version"),
                     namespace="")
    rag.add_document(_write(tmp_path, "doc.txt", "owner version"),
                     namespace="owner")
    hits = rag.chunks_for_mention("doc.txt", namespace="owner")
    contents = {h["content"] for h in hits}
    assert any("owner version" in c for c in contents)
    # re-ingesting owner's copy must not delete the shared one
    rag.add_document(_write(tmp_path, "doc.txt", "owner version two"),
                     namespace="owner")
    hits = rag.chunks_for_mention("doc.txt", namespace="8959938993")
    assert any("shared version" in h["content"] for h in hits)
