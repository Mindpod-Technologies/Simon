"""Simple local RAG for Simon: document ingestion + retrieval.

Documents are split into overlapping chunks, embedded locally via Ollama
(``nomic-embed-text``), and stored in the same SQLite database as memory.
Relevant chunks are injected into the system prompt each turn (see
``agent._build_messages``), so retrieval is deterministic — no tool call
required.

CLI (from ~/simon):
    .venv/bin/python -m simon.rag add path/to/file.md [more files...]
    .venv/bin/python -m simon.rag list
    .venv/bin/python -m simon.rag search "what is Angelmind?"
    .venv/bin/python -m simon.rag remove <source-name>
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

EMBED_MODEL = "nomic-embed-text"
EMBED_URL = "http://localhost:11434/api/embeddings"
CHUNK_SIZE = 900
CHUNK_OVERLAP = 150
MIN_SCORE = 0.35

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rag_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding TEXT NOT NULL,          -- JSON array of floats
    namespace TEXT NOT NULL DEFAULT '' -- '' = shared; else a session id
);
CREATE INDEX IF NOT EXISTS idx_rag_source ON rag_chunks(source);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS rag_fts USING fts5(
    source, chunk_index, content, contentless_delete=0);
"""


def _connect(path: Optional[str] = None) -> sqlite3.Connection:
    from simon import memory
    conn = memory._connect(path)
    conn.executescript(_SCHEMA)
    # Migration: namespace column on pre-2.0 databases.
    try:
        conn.execute("ALTER TABLE rag_chunks ADD COLUMN namespace TEXT"
                     " NOT NULL DEFAULT ''")
        conn.commit()
    except sqlite3.Error:  # column already exists
        pass
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_namespace"
                     " ON rag_chunks(namespace)")
        conn.commit()
    except sqlite3.Error:
        pass
    # FTS5 mirror for hybrid (keyword + vector) retrieval.
    try:
        conn.executescript(_FTS_SCHEMA)
        conn.commit()
    except sqlite3.Error:  # FTS5 unavailable — vector-only fallback
        pass
    return conn


def _fts_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1 FROM rag_fts LIMIT 1")
        return True
    except sqlite3.Error:
        return False


def embed(text: str) -> list[float]:
    """Embed text via the local Ollama embeddings endpoint."""
    resp = requests.post(EMBED_URL, json={"model": EMBED_MODEL, "prompt": text},
                         timeout=120)
    resp.raise_for_status()
    return resp.json()["embedding"]


def chunk_text(text: str, size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks on paragraph boundaries."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 > size and current:
            chunks.append(current.strip())
            current = current[-overlap:] + "\n\n" + para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text.strip()]


def add_document(path: str | Path, db_path: Optional[str] = None,
                 namespace: str = "") -> int:
    """Ingest one text/markdown file. Returns the number of chunks stored.
    ``namespace`` scopes visibility: '' = shared with everyone, otherwise
    only the owning session's turns retrieve it."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    source = path.name
    chunks = chunk_text(text)
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM rag_chunks WHERE source = ?"
                     " AND namespace = ?", (source, namespace))
        conn.execute("DELETE FROM rag_fts WHERE source = ?", (source,))
        has_fts = _fts_available(conn)
        for i, chunk in enumerate(chunks):
            vector = embed(chunk)
            conn.execute(
                "INSERT INTO rag_chunks (source, chunk_index, content,"
                " embedding, namespace) VALUES (?,?,?,?,?)",
                (source, i, chunk, json.dumps(vector), namespace),
            )
            if has_fts:
                conn.execute(
                    "INSERT INTO rag_fts (source, chunk_index, content)"
                    " VALUES (?,?,?)",
                    (source, i, chunk))
        conn.commit()
    finally:
        conn.close()
    return len(chunks)


def remove_document(source: str, db_path: Optional[str] = None) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute("DELETE FROM rag_chunks WHERE source = ?", (source,))
        conn.execute("DELETE FROM rag_fts WHERE source = ?", (source,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def list_documents(db_path: Optional[str] = None,
                   namespace: Optional[str] = None) -> list[tuple[str, int]]:
    conn = _connect(db_path)
    try:
        if namespace is None:
            rows = conn.execute(
                "SELECT source, COUNT(*) c FROM rag_chunks GROUP BY source"
                " ORDER BY source").fetchall()
        else:
            rows = conn.execute(
                "SELECT source, COUNT(*) c FROM rag_chunks"
                " WHERE namespace IN ('', ?) GROUP BY source"
                " ORDER BY source", (namespace,)).fetchall()
    finally:
        conn.close()
    return [(r["source"], r["c"]) for r in rows]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def search(query: str, k: int = 3, min_score: float = MIN_SCORE,
           db_path: Optional[str] = None,
           namespace: Optional[str] = None) -> list[dict]:
    """Return the top-k chunks relevant to ``query`` (may be empty).

    Hybrid retrieval: vector similarity fused with FTS5 keyword rank
    (reciprocal rank fusion), so exact strings (SKUs, names, error codes)
    land as reliably as fuzzy semantics. ``namespace`` scopes visibility:
    when given, only that session's chunks plus shared ('') are searched.
    """
    conn = _connect(db_path)
    try:
        if namespace is None:
            rows = conn.execute(
                "SELECT id, source, chunk_index, content, embedding"
                " FROM rag_chunks").fetchall()
        else:
            rows = conn.execute(
                "SELECT id, source, chunk_index, content, embedding"
                " FROM rag_chunks WHERE namespace IN ('', ?)",
                (namespace,)).fetchall()
        fts_rows = []
        if _fts_available(conn):
            try:
                terms = " OR ".join(
                    w for w in re.findall(r"[A-Za-z0-9_]+", query)[:8])
                if terms:
                    fts_rows = conn.execute(
                        "SELECT source, chunk_index,"
                        " bm25(rag_fts) AS rank FROM rag_fts"
                        " WHERE rag_fts MATCH ? ORDER BY rank LIMIT 20",
                        (terms,)).fetchall()
            except sqlite3.Error:
                fts_rows = []
    finally:
        conn.close()
    if not rows:
        return []

    # Vector candidates.
    vec_ranked = []
    try:
        qvec = embed(query)
        for row in rows:
            score = _cosine(qvec, json.loads(row["embedding"]))
            vec_ranked.append((row["source"], row["chunk_index"], score))
        vec_ranked.sort(key=lambda c: -c[2])
    except Exception as exc:  # noqa: BLE001 - RAG must never break a turn
        log.warning("rag embed failed: %s", exc)

    # Reciprocal rank fusion: 1/(60+rank) per signal, max vector score gate
    fused: dict[tuple, dict] = {}
    for rank, (source, idx, score) in enumerate(vec_ranked[:20]):
        key = (source, idx)
        fused.setdefault(key, {"source": source, "chunk_index": idx,
                               "vec": score, "rrf": 0.0})
        fused[key]["rrf"] += 1.0 / (60 + rank)
    for rank, row in enumerate(fts_rows):
        key = (row["source"], row["chunk_index"])
        if key not in fused:
            fused[key] = {"source": row["source"],
                          "chunk_index": row["chunk_index"],
                          "vec": 0.0, "rrf": 0.0}
        fused[key]["rrf"] += 1.0 / (60 + rank)
    ranked = sorted(fused.values(), key=lambda c: -c["rrf"])

    out = []
    content_by_key = {(r["source"], r["chunk_index"]): r["content"]
                      for r in rows}
    allowed = set(content_by_key)  # scoped rows only — FTS leaks must die here
    for cand in ranked[:k * 2]:
        if namespace is not None and \
                (cand["source"], cand["chunk_index"]) not in allowed:
            continue
        if cand["vec"] < min_score and cand["rrf"] < 0.024:
            continue  # no signal from either retriever
        out.append({"source": cand["source"],
                    "chunk_index": cand["chunk_index"],
                    "score": round(cand["vec"], 3),
                    "content": content_by_key.get(
                        (cand["source"], cand["chunk_index"]), "")})
        if len(out) >= k:
            break
    return out


def chunks_for_mention(text: str, k: int = 4,
                       db_path: Optional[str] = None,
                       namespace: Optional[str] = None) -> list[dict]:
    """Return chunks of any document whose filename is mentioned in ``text``.

    Deterministic complement to vector search: when the user names a file
    ("review test-brief.txt"), semantic similarity is unreliable — the
    question is ABOUT the document, not similar to it. Matches on the full
    source name or any dotted stem inside it (so "test-brief" matches
    "test-brief.txt.txt"). ``namespace`` scopes which spaces are visible.
    """
    low = (text or "").lower()
    if not low:
        return []
    mentioned: list[str] = []
    for source, _count in list_documents(db_path, namespace=namespace):
        names = {source.lower()}
        # progressive stems: "test-brief.txt.txt" → "test-brief.txt", …
        parts = source.lower().split(".")
        for i in range(1, len(parts)):
            names.add(".".join(parts[:i]))
        # Single bare words ("notes", "final") are too generic to match on
        # their own — require a dot (filename-like) or decent length.
        names = {n for n in names if n and ("." in n or len(n) >= 8)}
        if any(len(n) >= 4 and n in low for n in names):
            mentioned.append(source)
    if not mentioned:
        return []
    conn = _connect(db_path)
    try:
        out: list[dict] = []
        for source in mentioned:
            if namespace is None:
                rows = conn.execute(
                    "SELECT source, chunk_index, content FROM rag_chunks"
                    " WHERE source = ? ORDER BY chunk_index LIMIT ?",
                    (source, k)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT source, chunk_index, content FROM rag_chunks"
                    " WHERE source = ? AND namespace IN ('', ?)"
                    " ORDER BY chunk_index LIMIT ?",
                    (source, namespace, k)).fetchall()
            out.extend({"source": r["source"],
                        "chunk_index": r["chunk_index"], "score": 1.0,
                        "content": r["content"]} for r in rows)
        return out[:k]
    finally:
        conn.close()


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "add":
        for file in argv[2:]:
            n = add_document(file)
            print(f"ingested {file}: {n} chunks")
    elif cmd == "list":
        for source, count in list_documents():
            print(f"{source}: {count} chunks")
    elif cmd == "remove":
        print(f"removed {remove_document(argv[2])} chunks")
    elif cmd == "search":
        for hit in search(" ".join(argv[2:])):
            print(f"[{hit['score']}] {hit['source']}: {hit['content'][:150]}\n")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
