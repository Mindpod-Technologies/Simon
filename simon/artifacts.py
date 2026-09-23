"""Artifacts: files in Simon's workspace that the web UI can list, preview
and download.

An artifact is any regular file under the workspace root (excluding
internals like upload-extraction companions and hidden directories). The
chat UI renders ``[artifact:relpath]`` markers in Simon's replies as inline
images (charts) or download cards (documents) — tools that create files
should include the marker in their output so the model naturally quotes it.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Optional

# Directories never surfaced as artifacts (internal plumbing).
_SKIP_DIRS = {"uploads", ".git", "__pycache__", ".rag"}
# Extraction companions (foo.pdf.txt) are hidden behind their original.
_MAX_LIST = 200

_KIND_BY_EXT = {
    ".png": "chart", ".jpg": "chart", ".jpeg": "chart", ".gif": "chart",
    ".svg": "chart",
    ".md": "document", ".txt": "document", ".docx": "document",
    ".pdf": "document", ".html": "document",
    ".py": "code", ".js": "code", ".ts": "code", ".json": "code",
    ".yaml": "code", ".yml": "code", ".sh": "code",
    ".csv": "data", ".xlsx": "data", ".log": "data",
}

TEXT_PREVIEW_EXTS = {".md", ".txt", ".csv", ".json", ".log", ".py",
                     ".yaml", ".yml", ".html", ".sh"}


def workspace_root(settings) -> Path:
    root = Path(getattr(settings, "simon_workspace_dir", "./workspace"))
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def kind_for(path: Path) -> str:
    return _KIND_BY_EXT.get(path.suffix.lower(), "other")


def list_artifacts(settings, limit: int = _MAX_LIST) -> list[dict]:
    """All artifact files under the workspace, newest first. Text artifacts
    carry a readable snippet — the tray shows a title + two-line preview,
    not a bare filename."""
    root = workspace_root(settings)
    out: list[dict] = []
    for path in sorted(root.rglob("*")):
        if len(out) >= limit:
            break
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        parts = rel.parts
        if any(part.startswith(".") for part in parts):
            continue
        if parts[0] in _SKIP_DIRS:
            continue
        # Hide upload-extraction companions (foo.pdf.txt next to foo.pdf).
        if path.suffix == ".txt" and path.with_suffix("").exists():
            continue
        stat = path.stat()
        snippet = ""
        if path.suffix.lower() in (".md", ".txt", ".csv", ".json", ".log",
                                   ".py", ".yaml", ".yml", ".html", ".sh"):
            try:
                import re as _re
                raw = path.read_text(encoding="utf-8", errors="replace")
                lines = [ln.strip() for ln in raw.splitlines()
                         if ln.strip() and not ln.strip().startswith("#")]
                # Snippets are plain prose — markdown marks are noise there.
                plain = _re.sub(r"[*`#>]", "", " ".join(lines))
                snippet = plain[:180].strip()
            except OSError:
                snippet = ""
        out.append({
            "path": rel.as_posix(),
            "name": path.name,
            "size": stat.st_size,
            "modified": int(stat.st_mtime),
            "kind": kind_for(path),
            "snippet": snippet,
        })
    return sorted(out, key=lambda a: -a["modified"])


def resolve_artifact(settings, relpath: str) -> Optional[Path]:
    """Resolve an artifact path inside the workspace — traversal-proof."""
    root = workspace_root(settings)
    candidate = (root / relpath).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def media_type_for(path: Path) -> str:
    """Best-effort content type for inline preview."""
    if path.suffix.lower() in TEXT_PREVIEW_EXTS:
        return "text/plain; charset=utf-8"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"
