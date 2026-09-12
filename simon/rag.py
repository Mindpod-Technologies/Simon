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
    embedding TEXT NOT NULL          -- JSON array of floats
);
CREATE INDEX IF NOT EXISTS idx_rag_source ON rag_chunks(source);
"""


def _connect(path: Optional[str] = None) -> sqlite3.Connection:
    from simon import memory
    conn = memory._connect(path)
    conn.executescript(_SCHEMA)
    return conn


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


def add_document(path: str | Path, db_path: Optional[str] = None) -> int:
    """Ingest one text/markdown file. Returns the number of chunks stored."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    source = path.name
    chunks = chunk_text(text)
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM rag_chunks WHERE source = ?", (source,))
        for i, chunk in enumerate(chunks):
            vector = embed(chunk)
            conn.execute(
                "INSERT INTO rag_chunks (source, chunk_index, content,"
                " embedding) VALUES (?,?,?,?)",
                (source, i, chunk, json.dumps(vector)),
            )
        conn.commit()
    finally:
        conn.close()
    return len(chunks)


def remove_document(source: str, db_path: Optional[str] = None) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute("DELETE FROM rag_chunks WHERE source = ?", (source,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def list_documents(db_path: Optional[str] = None) -> list[tuple[str, int]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT source, COUNT(*) c FROM rag_chunks GROUP BY source"
            " ORDER BY source").fetchall()
    finally:
        conn.close()
    return [(r["source"], r["c"]) for r in rows]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def search(query: str, k: int = 3, min_score: float = MIN_SCORE,
           db_path: Optional[str] = None) -> list[dict]:
    """Return the top-k chunks relevant to ``query`` (may be empty)."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT source, chunk_index, content, embedding FROM rag_chunks"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []
    try:
        qvec = embed(query)
    except Exception as exc:  # noqa: BLE001 - RAG must never break a turn
        log.warning("rag embed failed: %s", exc)
        return []
    scored = []
    for row in rows:
        score = _cosine(qvec, json.loads(row["embedding"]))
        if score >= min_score:
            scored.append({"source": row["source"], "score": round(score, 3),
                           "content": row["content"]})
    scored.sort(key=lambda c: -c["score"])
    return scored[:k]


def chunks_for_mention(text: str, k: int = 4,
                       db_path: Optional[str] = None) -> list[dict]:
    """Return chunks of any document whose filename is mentioned in ``text``.

    Deterministic complement to vector search: when the user names a file
    ("review test-brief.txt"), semantic similarity is unreliable — the
    question is ABOUT the document, not similar to it. Matches on the full
    source name or any dotted stem inside it (so "test-brief" matches
    "test-brief.txt.txt").
    """
    low = (text or "").lower()
    if not low:
        return []
    mentioned: list[str] = []
    for source, _count in list_documents(db_path):
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
            rows = conn.execute(
                "SELECT source, chunk_index, content FROM rag_chunks"
                " WHERE source = ? ORDER BY chunk_index LIMIT ?",
                (source, k)).fetchall()
            out.extend({"source": r["source"], "score": 1.0,
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
