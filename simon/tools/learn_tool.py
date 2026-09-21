"""Self-learning tools: let Simon ingest his own notes into memory.

The expertise loop: a scheduled study task researches a topic, writes a
dated digest, and calls ingest_note — the digest becomes retrievable
knowledge with source citations on every later turn. This is how Simon
compounds expertise on whatever the owner assigns him.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .base import Tool

log = logging.getLogger(__name__)


def _safe_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", (name or "").strip().lower())
    return slug.strip("-")[:80] or "note"


def register_learn_tools(registry, settings) -> None:
    workspace = Path(getattr(settings, "simon_workspace_dir", "")
                     or "workspace")

    def ingest_note(name: str, text: str) -> str:
        """Save a named note into the workspace AND into RAG memory (shared
        space) so it is retrievable with citations on future turns."""
        name = _safe_name(name)
        if not name.endswith(".md"):
            name += ".md"
        text = (text or "").strip()
        if len(text) < 20:
            return "Error: note text too short — ingest the full digest."
        try:
            notes_dir = workspace / "notes"
            notes_dir.mkdir(parents=True, exist_ok=True)
            dest = notes_dir / name
            dest.write_text(text, encoding="utf-8")
            from .. import rag
            chunks = rag.add_document(dest, namespace="")
            log.info("ingested note %s (%d chunks)", name, chunks)
            return (f"Ingested '{name}' into long-term memory ({chunks} "
                    f"chunks, shared space). It is now citable knowledge.")
        except Exception as exc:  # noqa: BLE001 - tools must never raise
            return f"Error: ingest failed: {exc}"

    registry.register(Tool(
        name="ingest_note",
        description=(
            "Save knowledge into long-term memory: writes the text as a "
            "workspace note and indexes it for retrieval with citations. "
            "Use after research/study digests, lessons learned, or any "
            "finding worth keeping forever."),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "Note filename, e.g. "
                                        "bento-digest-2026-09-21"},
                "text": {"type": "string",
                         "description": "The full note content to remember."},
            },
            "required": ["name", "text"],
        },
        func=ingest_note,
    ))
