"""Per-task workspaces: named projects, each with its own conversation
session and workspace directory — the Simon Work equivalent of Kimi's
task directories.

Each task maps ``session_id`` → ``workspace/tasks/<slug>/``. The web UI
offers a task switcher; agents serving a task session get a settings copy
whose ``simon_workspace_dir`` points at the task directory, so files Simon
reads/writes stay scoped to that project.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from . import artifacts, memory

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    session_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

TASKS_DIRNAME = "tasks"


def _connect() -> sqlite3.Connection:
    conn = memory._connect()
    conn.executescript(_SCHEMA)
    return conn


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or f"task-{int(time.time())}"


def session_for(slug: str) -> str:
    return f"task-{slug}"


def create_task(name: str, settings) -> dict:
    """Create a task (idempotent by slug) and its workspace directory."""
    name = (name or "").strip()
    if not name:
        raise ValueError("task name must not be empty")
    slug = slugify(name)
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO tasks (slug, name, session_id) "
            "VALUES (?, ?, ?)",
            (slug, name, session_for(slug)))
        conn.commit()
    finally:
        conn.close()
    workspace_for(slug, settings)  # ensure the directory exists
    return {"slug": slug, "name": name, "session_id": session_for(slug)}


def list_tasks(settings) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT slug, name, session_id, created_at FROM tasks "
            "ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()
    root = tasks_root(settings)
    out = []
    for row in rows:
        task_dir = root / row["slug"]
        file_count = sum(1 for p in task_dir.rglob("*")
                         if p.is_file()) if task_dir.is_dir() else 0
        out.append({"slug": row["slug"], "name": row["name"],
                    "session_id": row["session_id"],
                    "created_at": row["created_at"],
                    "files": file_count})
    return out


def delete_task(slug: str, settings) -> bool:
    """Remove a task record and its workspace directory."""
    import shutil
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM tasks WHERE slug = ?", (slug,))
        conn.commit()
        removed = cur.rowcount > 0
    finally:
        conn.close()
    task_dir = (tasks_root(settings) / slug).resolve()
    if task_dir.parent == tasks_root(settings).resolve() and task_dir.is_dir():
        shutil.rmtree(task_dir)
        removed = True
    return removed


def tasks_root(settings) -> Path:
    root = artifacts.workspace_root(settings) / TASKS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def workspace_for(slug: str, settings) -> Path:
    """The workspace directory for a task slug (created on demand)."""
    slug = slugify(slug)
    path = tasks_root(settings) / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_workspace(session_id: str, settings) -> Optional[Path]:
    """Workspace override for a session id, or None for regular sessions."""
    if not (session_id or "").startswith("task-"):
        return None
    return workspace_for(session_id[len("task-"):], settings)
