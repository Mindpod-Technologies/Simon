"""Skills: drop-in instruction packs that teach Simon workflows.

A skill is a directory containing a ``SKILL.md`` file with a tiny YAML-ish
frontmatter block and a Markdown body::

    ---
    name: web-research
    description: Research a topic on the web and write a cited report.
    ---

    # Web Research
    1. Clarify the question ...
    (procedure Simon follows)

Two locations are scanned:

* ``<repo>/skills/``      — built-in, versioned, shipped with releases;
* ``<repo>/data/skills/`` — customer skills. ``data/`` is gitignored, so
  these survive ``run.py update`` untouched. A custom skill with the same
  name as a built-in OVERRIDES it (customers can tune vendor skills).

The agent's system prompt carries a compact index (name + one-line
description). When a turn matches a skill, the model calls the
``load_skill`` tool, receives the full procedure, and follows it — the
same pattern as Claude/Kimi skill systems, but dependency-free: SKILL.md
parsing here is a 20-line frontmatter reader, no PyYAML required.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILTIN_DIR = REPO_ROOT / "skills"
CUSTOM_DIR = REPO_ROOT / "data" / "skills"

_CACHE_TTL_S = 30
_cache: tuple[float, list["Skill"]] | None = None


@dataclass
class Skill:
    name: str
    description: str
    body: str          # Markdown procedure (frontmatter stripped)
    path: Path         # the SKILL.md file
    source: str        # "builtin" | "custom"

    @property
    def aux_files(self) -> list[str]:
        """Non-SKILL.md files bundled in the skill directory."""
        try:
            return sorted(p.name for p in self.path.parent.iterdir()
                          if p.is_file() and p.name != "SKILL.md")
        except OSError:
            return []


def _parse_skill_md(text: str) -> tuple[dict[str, str], str]:
    """Split frontmatter from body. Minimal `key: value` parser — skills
    must not require a YAML dependency to be usable."""
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip().lower()] = v.strip().strip('"').strip("'")
            body = text[end + 4:].lstrip("\n")
    return meta, body


def _scan_dir(directory: Path, source: str) -> list[Skill]:
    skills = []
    if not directory.is_dir():
        return skills
    for child in sorted(directory.iterdir()):
        skill_md = child / "SKILL.md"
        if not child.is_dir() or not skill_md.is_file():
            continue
        try:
            meta, body = _parse_skill_md(skill_md.read_text())
        except Exception as exc:  # noqa: BLE001
            log.warning("skills: cannot read %s: %s", skill_md, exc)
            continue
        name = meta.get("name") or child.name
        desc = meta.get("description") or "(no description)"
        skills.append(Skill(name=name, description=desc, body=body,
                            path=skill_md, source=source))
    return skills


def discover(force: bool = False) -> list[Skill]:
    """All skills, custom overriding builtin by name. Cached for 30 s so
    per-turn index injection costs nothing but edits appear quickly."""
    global _cache
    if not force and _cache and time.time() - _cache[0] < _CACHE_TTL_S:
        return _cache[1]
    by_name: dict[str, Skill] = {}
    for skill in _scan_dir(BUILTIN_DIR, "builtin"):
        by_name[skill.name] = skill
    for skill in _scan_dir(CUSTOM_DIR, "custom"):   # custom wins
        by_name[skill.name] = skill
    skills = sorted(by_name.values(), key=lambda s: s.name)
    _cache = (time.time(), skills)
    return skills


def render_index(skills: list[Skill]) -> str:
    """Compact index for the system prompt."""
    lines = [
        "Available skills — when the user's request clearly matches one, "
        "call the load_skill tool FIRST, then follow its procedure:"
    ]
    for s in skills:
        tag = "" if s.source == "builtin" else " [custom]"
        lines.append(f"- {s.name}: {s.description}{tag}")
    return "\n".join(lines)


def load_body(name: str) -> str:
    """Full skill content for the load_skill tool (fresh from disk, so
    edits apply immediately without waiting for the index cache)."""
    for skill in discover():
        if skill.name == name:
            # Re-read: the cached body may be stale relative to an edit.
            try:
                _, body = _parse_skill_md(skill.path.read_text())
            except Exception:  # noqa: BLE001
                body = skill.body
            out = f"# Skill: {skill.name}\n\n{body.strip()}"
            if skill.aux_files:
                out += ("\n\nBundled files in this skill's directory "
                        f"({skill.path.parent}):\n"
                        + "\n".join(f"- {f}" for f in skill.aux_files))
            return out
    known = ", ".join(s.name for s in discover()) or "(none)"
    return f"Error: no skill named '{name}'. Available: {known}"


def reset_cache_for_tests() -> None:
    global _cache
    _cache = None
