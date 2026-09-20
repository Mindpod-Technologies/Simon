"""Sandbox tool: run code in a throwaway, isolated container.

Distinct from the workspace tools: file tools READ/WRITE the workspace;
sandbox_exec RUNS untrusted-ish code with no network, capped CPU/memory,
and only the workspace mounted. When Docker is unavailable the tool is
simply not registered (Simon never pretends to have a sandbox he lacks).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import Tool

log = logging.getLogger(__name__)

_IMAGE = "python:3.11-slim"
_TIMEOUT_S = 60
_OUTPUT_CAP = 4000


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10,
                       check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def sandbox_exec(code: str, workspace: str, timeout_s: int = _TIMEOUT_S) -> str:
    """Run ``code`` (Python) inside an isolated container.

    No network, 512 MB RAM, 1 CPU, read-write mount limited to the
    workspace at /work. Returns combined stdout/stderr (capped).
    """
    if not code or not code.strip():
        return "Error: sandbox_exec needs code to run."
    ws = Path(workspace).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".py", dir=ws,
                                     delete=False) as fh:
        fh.write(code)
        script = Path(fh.name)
    try:
        cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "--memory", "512m", "--cpus", "1",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,size=64m",
            "-v", f"{ws}:/work",
            "-w", "/work",
            _IMAGE, "python", f"/work/{script.name}",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return f"Error: sandbox run exceeded {timeout_s}s and was killed."
        out = (proc.stdout or "") + (proc.stderr or "")
        out = out.strip() or "(no output)"
        if proc.returncode != 0:
            return f"Error (exit {proc.returncode}):\n{out[:_OUTPUT_CAP]}"
        return out[:_OUTPUT_CAP]
    finally:
        script.unlink(missing_ok=True)


def register_sandbox_tool(registry, settings) -> None:
    """Register sandbox_exec when Docker is present; otherwise skip."""
    if not getattr(settings, "simon_sandbox_enabled", True):
        log.info("sandbox tool skipped: SIMON_SANDBOX_ENABLED is false")
        return
    if not docker_available():
        log.info("sandbox tool skipped: Docker not available "
                 "(install Docker Desktop to enable isolated code runs)")
        return

    workspace = getattr(settings, "simon_workspace_dir", "") or "workspace"

    def _exec(code: str) -> str:
        try:
            return sandbox_exec(code, workspace)
        except Exception as exc:  # noqa: BLE001 - tools must never raise
            return f"Error: sandbox failed: {exc}"

    registry.register(Tool(
        name="sandbox_exec",
        description=(
            "Run Python code in an isolated sandbox (Docker container: no "
            "network, capped CPU/RAM, only the workspace mounted at /work). "
            "Use for calculations, data transforms, and trying code — NOT "
            "for web access (impossible there) or workspace edits (use the "
            "file tools)."),
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string",
                         "description": "Python source to execute."},
            },
            "required": ["code"],
        },
        func=_exec,
    ))
    log.info("sandbox tool registered (docker backend)")
