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

    def teach_skill(name: str, description: str, procedure: str,
                    overwrite: bool = False) -> str:
        """Distill a demonstrated workflow into a reusable SKILL.md.

        Writes a customer skill (data/skills/<name>/SKILL.md) — discovered
        by the skills index immediately and loadable on any future turn.
        """
        slug = _safe_name(name)
        if not slug or slug == "note":
            return "Error: give the skill a proper name."
        description = (description or "").strip()
        procedure = (procedure or "").strip()
        if len(description) < 10:
            return "Error: description too short — one line on when to use it."
        if len(procedure) < 60:
            return ("Error: procedure too short — write the actual steps "
                    "(numbered), not a summary.")
        data_skills = Path("data") / "skills" / slug
        target = data_skills / "SKILL.md"
        if target.exists() and not overwrite:
            return (f"Error: a skill named '{slug}' already exists. Ask the "
                    "owner whether to replace it, then call teach_skill "
                    "again with overwrite=true.")
        try:
            data_skills.mkdir(parents=True, exist_ok=True)
            target.write_text(
                f"---\nname: {slug}\ndescription: {description}\n---\n\n"
                f"# {slug.replace('-', ' ').title()} Procedure\n\n"
                f"{procedure}\n",
                encoding="utf-8")
            from .. import skills as skills_mod
            skills_mod.discover(force=True)  # refresh the index now
            log.info("taught skill %s", slug)
            return (f"Skill '{slug}' learned and live. Load it anytime with "
                    f"load_skill — it now appears in the skills index.")
        except Exception as exc:  # noqa: BLE001 - tools must never raise
            return f"Error: could not save the skill: {exc}"

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

    registry.register(Tool(
        name="teach_skill",
        description=(
            "Turn a workflow the user just demonstrated in chat into a "
            "reusable named skill (a SKILL.md the owner can invoke later by "
            "name). Use ONLY after the user has walked through the steps "
            "and you have played them back accurately — never distill a "
            "skill from a vague mention."),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "Skill slug, e.g. invoice-triage"},
                "description": {"type": "string",
                                "description": "One line: when to use it."},
                "procedure": {"type": "string",
                              "description": "The numbered steps, in full."},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["name", "description", "procedure"],
        },
        func=teach_skill,
    ))
