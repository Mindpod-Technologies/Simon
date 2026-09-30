"""Simon's own desktop VM (the "watch him work" machine).

A Kasm/Webtop container gives Simon a full Linux desktop he can drive —
and the owner a live browser view (http://localhost:3000) to watch him
work, Cursor-style but with a whole desktop. Nobody else in the category
makes the agent's computer watchable; this is the trust feature.

Tools (registered only when the desktop container is running):
- vm_status      — is the desktop up, and where to watch it
- vm_exec        — run a shell command inside the desktop
- vm_open_url    — open a URL in the desktop's browser
- vm_screenshot  — capture the desktop screen (lands in Artifacts)

All execution happens INSIDE the container — the host Mac is never
touched. The watch URL is loopback-only.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from .base import Tool

log = logging.getLogger(__name__)

CONTAINER = "simon-desktop"
WATCH_URL = "http://localhost:3000"


def _docker_ok() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return True
    except Exception:  # noqa: BLE001
        return False


def _desktop_running() -> bool:
    if not _docker_ok():
        return False
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
            capture_output=True, text=True, timeout=10)
        return out.stdout.strip() == "true"
    except Exception:  # noqa: BLE001
        return False


def _exec(cmd: str, timeout: int = 60) -> str:
    out = subprocess.run(
        ["docker", "exec", CONTAINER, "/bin/bash", "-lc", cmd],
        capture_output=True, text=True, timeout=timeout)
    text = (out.stdout + ("\n" + out.stderr if out.stderr else "")).strip()
    return text or "(no output)"


def _vm_status() -> str:
    if not _docker_ok():
        return "Docker is not running. Start Docker Desktop to wake my desktop."
    if not _desktop_running():
        return ("My desktop VM is not running. Start it with: docker start "
                f"{CONTAINER} (or the deploy/simon-desktop.yml compose file).")
    return (f"My desktop is up. Watch me work live at {WATCH_URL} "
            f"(container: {CONTAINER}).")


def _vm_exec(command: str) -> str:
    if not _desktop_running():
        return "Error: desktop VM is not running (see vm_status)."
    try:
        return _exec(command)
    except subprocess.TimeoutExpired:
        return "Error: command timed out inside the desktop"
    except Exception as exc:  # noqa: BLE001
        return f"Error: vm_exec failed: {exc}"


def _vm_open_url(url: str) -> str:
    if not _desktop_running():
        return "Error: desktop VM is not running (see vm_status)."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    out = _vm_exec(f"nohup xdg-open '{url}' >/dev/null 2>&1 & echo opened")
    return (f"{out}\nThe page is open on my desktop — watch live at "
            f"{WATCH_URL}")


def _vm_screenshot() -> str:
    """Capture the desktop to an artifact the chat UI renders inline."""
    if not _desktop_running():
        return "Error: desktop VM is not running (see vm_status)."
    name = f"vm-{int(time.time())}.png"
    try:
        _exec(f"scrot -o /tmp/{name} 2>/dev/null || "
              f"import -window root /tmp/{name}")
        out_dir = Path("workspace") / "screenshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / name
        subprocess.run(["docker", "cp", f"{CONTAINER}:/tmp/{name}",
                        str(dest)], capture_output=True, timeout=30)
        if not dest.exists() or dest.stat().st_size == 0:
            return "Error: screenshot produced no image"
        return f"Desktop screenshot captured. [artifact:screenshots/{name}]"
    except Exception as exc:  # noqa: BLE001
        return f"Error: vm_screenshot failed: {exc}"


def register_vm_tools(registry, settings) -> None:
    """Register the VM toolset. Availability is checked lazily AT CALL TIME —
    never probe Docker during registry construction (a probe there ran on
    every agent turn and collided with anything else holding subprocess).
    Without Docker or the container, each tool answers with setup guidance."""
    if not getattr(settings, "simon_vm_enabled", True):
        return

    registry.register(Tool(
        name="vm_status",
        description="Whether Simon's own desktop VM is running, and the "
                    "loopback URL where the owner can watch it live.",
        parameters={"type": "object", "properties": {}},
        func=lambda: _vm_status(),
    ))
    registry.register(Tool(
        name="vm_exec",
        description="Run a shell command inside Simon's isolated desktop "
                    "VM (never on the host Mac).",
        parameters={"type": "object", "properties": {
            "command": {"type": "string"}}, "required": ["command"]},
        func=lambda command: _vm_exec(command),
    ))
    registry.register(Tool(
        name="vm_open_url",
        description="Open a URL in the desktop VM's browser — the owner can "
                    "watch at the loopback URL in vm_status.",
        parameters={"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]},
        func=lambda url: _vm_open_url(url),
    ))
    registry.register(Tool(
        name="vm_screenshot",
        description="Capture a screenshot of Simon's desktop VM; the image "
                    "lands in Artifacts and in the chat.",
        parameters={"type": "object", "properties": {}},
        func=lambda: _vm_screenshot(),
    ))
