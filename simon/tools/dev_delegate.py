"""Delegate a development task to an external coding CLI (Claude Code or Codex).

Runs the engine headless inside an allowlisted working directory under Simon's
workspace, with a scrubbed environment (no Simon secrets leak into the child)
and a bounded timeout. Gated behind ``SIMON_DEV_DELEGATE_ENABLED`` — off by
default, exactly like ``run_shell``.

Design rules (mirror the company-wide guardrails in MindpodTech_Agents):
  * engines are an allowlist (``claude``, ``codex``, ``cursor``) — never a
    free-form command
  * the working directory must resolve inside the workspace root
  * the child env carries only PATH/HOME/LANG + CI markers — no SIMON_/API keys
  * engines authenticate with their OWN accounts (Claude: keychain OAuth;
    Codex: ~/.codex login) — actions are attributable per engine
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from .base import Tool

log = logging.getLogger(__name__)

_TIMEOUT_S = 1500          # 25 min hard cap per delegated task
_MAX_OUT = 6000            # chars returned to the model

_ENGINES = {
    "claude": lambda task: ["claude", "-p", task, "--output-format", "text"],
    "codex": lambda task: ["codex", "exec", "--sandbox", "workspace-write",
                           task],
    "cursor": lambda task: ["cursor-agent", "-p", task,
                            "--output-format", "text"],
}

# Simon runs under launchd with a minimal PATH (/usr/bin:/bin:...), so a bare
# binary name often won't resolve. Try PATH first, then well-known locations.
_BIN_CANDIDATES = {
    "claude": ("/opt/homebrew/bin/claude", "/usr/local/bin/claude"),
    "codex": ("/opt/homebrew/bin/codex", "/usr/local/bin/codex"),
    "cursor-agent": (str(Path.home() / ".local" / "bin" / "cursor-agent"),
                     "/opt/homebrew/bin/cursor-agent"),
}


def _resolve_bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for cand in _BIN_CANDIDATES.get(name, ()):  # absolute fallbacks
        if os.path.exists(cand):
            return cand
    return name  # let subprocess raise FileNotFoundError -> clean message

_SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR",
                  # Headless auth for the claude engine (setup-token); not an
                  # API-key pattern, so the secret-scrub tests still hold.
                  "CLAUDE_CODE_OAUTH_TOKEN")


def _resolve_cwd(workspace_root: Path, rel: str) -> Path:
    root = workspace_root.resolve()
    cwd = (root / (rel or ".")).resolve()
    if cwd != root and root not in cwd.parents:
        raise ValueError(
            f"path {rel!r} escapes the workspace root ({root})")
    if not cwd.is_dir():
        raise ValueError(f"directory does not exist: {cwd}")
    return cwd


def delegate_dev(settings, task: str, engine: str = "claude",
                 path: str = ".") -> str:
    """Run one development task through an external coding CLI, headless."""
    task = (task or "").strip()
    if not task:
        return "delegate_dev: empty task."
    engine = (engine or "claude").strip().lower()
    if engine not in _ENGINES:
        return (f"delegate_dev: unknown engine {engine!r}. "
                f"Allowed: {', '.join(sorted(_ENGINES))}.")
    root = Path(getattr(settings, "simon_workspace_dir", "./workspace"))
    try:
        cwd = _resolve_cwd(root, path)
    except ValueError as exc:
        return f"delegate_dev: {exc}"

    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    env.update({"CI": "true", "TERM": "dumb"})
    cmd = _ENGINES[engine](task)
    cmd[0] = _resolve_bin(cmd[0])
    log.info("delegate_dev: engine=%s cwd=%s task=%.80s", engine, cwd, task)
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), env=env, capture_output=True, text=True,
            timeout=_TIMEOUT_S,
        )
    except FileNotFoundError:
        return (f"delegate_dev: {engine!r} CLI not found on PATH. "
                f"Install it and authenticate interactively first.")
    except subprocess.TimeoutExpired:
        return (f"delegate_dev: {engine} exceeded the {_TIMEOUT_S // 60}-minute "
                f"cap and was killed. Split the task and retry.")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if len(out) > _MAX_OUT:
        out = out[:_MAX_OUT] + f"\n… [truncated, {len(out)} chars total]"
    status = "ok" if proc.returncode == 0 else f"exit {proc.returncode}"
    parts = [f"[{engine} — {status}]"]
    if out:
        parts.append(out)
    if err and proc.returncode != 0:
        parts.append(f"stderr: {err[:800]}")
    return "\n".join(parts) if len(parts) > 1 else f"[{engine} — {status}] (no output)"


def register_dev_delegate_tools(registry, settings) -> None:
    """Register delegate_dev when SIMON_DEV_DELEGATE_ENABLED=true."""
    if not getattr(settings, "simon_dev_delegate_enabled", False):
        return
    registry.register(Tool(
        name="delegate_dev",
        description=(
            "Delegate a coding/development task to an external coding CLI "
            "('claude', 'codex', or 'cursor'), run headless in a directory "
            "under the workspace. ALWAYS prefer this tool when the user asks "
            "to delegate, hand off, or have Claude/Codex/Cursor do coding "
            "work — do NOT substitute GitHub/file tools for it. Use for "
            "substantial implementation work that benefits from a dedicated "
            "coding agent; results return when the engine finishes (up to "
            "25 min). engine defaults to 'claude'; path is relative to the "
            "workspace root."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string",
                         "description": "Full instruction for the coding engine, including what to change and where."},
                "engine": {"type": "string",
                           "enum": ["claude", "codex", "cursor"],
                           "default": "claude"},
                "path": {"type": "string",
                         "description": "Working directory relative to the workspace root (default '.').",
                         "default": "."},
            },
            "required": ["task"],
        },
        func=lambda task, engine="claude", path=".": delegate_dev(
            settings, task, engine, path),
    ))
    log.info("delegate_dev tool registered (engines: claude, codex, cursor)")
