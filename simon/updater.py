"""Self-update mechanism: git-tag based updates with smoke tests and
automatic rollback.

Design (see COMMERCIAL.md §6): customers pull semver tags from a private
git remote. `run.py update` fetches tags, checks out the newest one,
refreshes dependencies if requirements.txt changed, runs the unit-test
suite as a smoke gate, restarts the launchd services, and health-checks
the web UI. Any failure after the checkout triggers an automatic rollback
to the previously running commit.

State is kept in ``data/update-state.json`` so `run.py rollback` works
even days later. Nothing here ever touches ``.env``, ``data/``,
``workspace/`` or ``mcp.json`` — those are gitignored runtime state.

Update sources, in order:
  1. ``git fetch --tags origin`` when an origin remote exists (customer
     mirror / private repo);
  2. local tags only, otherwise (useful for vendor-shipped bundles).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent
STATE_FILE = REPO / "data" / "update-state.json"
SERVICES = ("com.simon.assistant", "com.simon.monitor")
HEALTH_URL = "http://localhost:8788/"
HEALTH_TIMEOUT_S = 45

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class UpdateError(RuntimeError):
    """A user-facing update failure (already printed by the caller)."""


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #

def _git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise UpdateError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def _semver_key(tag: str) -> tuple[int, int, int] | None:
    m = _SEMVER.match(tag)
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def _latest_tag() -> str | None:
    tags = [t for t in _git("tag").splitlines() if _semver_key(t)]
    return max(tags, key=_semver_key) if tags else None  # type: ignore[arg-type]


def _current_ref() -> tuple[str, str]:
    """(short sha, tag exactly at HEAD or '')."""
    sha = _git("rev-parse", "--short", "HEAD")
    tag = _git("describe", "--tags", "--exact-match", "HEAD", check=False)
    return sha, tag


def _req_hash() -> str:
    req = REPO / "requirements.txt"
    return hashlib.sha256(req.read_bytes()).hexdigest() if req.exists() else ""


def _save_state(sha: str, tag: str, req_hash: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(
        {"previous_sha": sha, "previous_tag": tag,
         "previous_req_hash": req_hash, "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
        indent=2))


def _load_state() -> dict | None:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return None


def _pip_install() -> None:
    pip = REPO / ".venv" / "bin" / "pip"
    if not pip.exists():
        raise UpdateError(f"virtualenv pip not found at {pip}")
    proc = subprocess.run(
        [str(pip), "install", "-q", "-r", "requirements.txt"],
        cwd=REPO, capture_output=True, text=True)
    if proc.returncode != 0:
        raise UpdateError(f"dependency install failed: {proc.stderr[-500:]}")


def _run_tests() -> None:
    """Unit-test smoke gate, run from a scratch cwd against the repo code
    (mirrors how the suite is run manually) so tests never touch live data."""
    pytest = REPO / ".venv" / "bin" / "python"
    scratch = Path(tempfile.mkdtemp(prefix="simon-update-test-"))
    try:
        proc = subprocess.run(
            [str(pytest), "-m", "pytest", str(REPO / "tests"), "-q",
             "--rootdir", str(REPO)],
            cwd=scratch, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(REPO)},
            timeout=600)
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        if proc.returncode != 0:
            raise UpdateError("unit tests failed: " + " | ".join(tail))
        log.info("tests: %s", tail[-1] if tail else "ok")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _restart_services() -> None:
    """Restart Simon's background services on whatever OS we're on."""
    import platform
    system = platform.system()
    if system == "Darwin":
        uid = subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()
        for label in SERVICES:
            subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
                           capture_output=True)
    elif system == "Windows":
        for task in ("SimonAssistant", "SimonMonitor"):
            for verb in ("Stop", "Start"):
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"{verb}-ScheduledTask -TaskName {task}"],
                    capture_output=True)
    else:  # Linux: systemd user units per deploy/simon.service
        for unit in ("simon", "simon-monitor"):
            subprocess.run(["systemctl", "--user", "restart", unit],
                           capture_output=True)


def _healthy() -> bool:
    deadline = time.time() + HEALTH_TIMEOUT_S
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


# ---------------------------------------------------------------------- #
# Public operations
# ---------------------------------------------------------------------- #

def preflight() -> None:
    if not (REPO / ".git").is_dir():
        raise UpdateError(
            f"{REPO} is not a git repository — updates need the git "
            "distribution (see COMMERCIAL.md §6).")
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise UpdateError(
            "tracked files have local modifications — commit or stash them "
            "first:\n" + dirty)


def fetch() -> str:
    """Fetch tags from origin when present; return a note for the log."""
    remotes = _git("remote")
    if "origin" in remotes.split():
        proc = subprocess.run(
            ["git", "-C", str(REPO), "fetch", "--tags", "origin"],
            capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            log.warning("fetch from origin failed (offline?) — using local tags: %s",
                        proc.stderr.strip()[:200])
            return "local tags (fetch failed)"
        return "origin"
    return "local tags (no origin remote)"


def check() -> dict:
    """Report update availability without changing anything."""
    preflight()
    source = fetch()
    sha, tag = _current_ref()
    latest = _latest_tag()
    current_key = _semver_key(tag) if tag else None
    latest_key = _semver_key(latest) if latest else None
    available = bool(latest_key and (not current_key or latest_key > current_key))
    return {"current_sha": sha, "current_tag": tag or "(untagged)",
            "latest_tag": latest or "(none)", "source": source,
            "update_available": available}


def update(skip_tests: bool = False) -> None:
    """Full update with automatic rollback on failure."""
    info = check()
    print(f"current: {info['current_tag']} ({info['current_sha']})  "
          f"latest: {info['latest_tag']}  [{info['source']}]")
    if not info["update_available"]:
        print("Simon is up to date.")
        return

    prev_sha_full = _git("rev-parse", "HEAD")
    _, prev_tag = _current_ref()
    prev_req = _req_hash()
    _save_state(prev_sha_full, prev_tag, prev_req)
    target = info["latest_tag"]
    print(f"updating to {target} (rollback point: {prev_tag or prev_sha_full[:8]}) ...")

    try:
        _git("checkout", "-q", target)
        if _req_hash() != prev_req:
            print("requirements changed — refreshing dependencies ...")
            _pip_install()
        if not skip_tests:
            print("running unit-test smoke gate ...")
            _run_tests()
        print("restarting services ...")
        _restart_services()
        if not _healthy():
            raise UpdateError("health check failed after update")
    except Exception as exc:
        print(f"update failed: {exc}")
        print("rolling back ...")
        rollback()
        raise UpdateError(
            f"update to {target} failed and was rolled back; Simon is "
            "running the previous version.") from exc

    sha, tag = _current_ref()
    print(f"done — Simon is on {tag or sha} and healthy.")


def rollback() -> None:
    """Return to the version recorded before the last update attempt."""
    state = _load_state()
    if not state:
        raise UpdateError("no rollback point recorded (data/update-state.json missing).")
    target = state["previous_tag"] or state["previous_sha"]
    print(f"rolling back to {target} (recorded {state.get('ts', '?')}) ...")
    _git("checkout", "-q", target)
    if _req_hash() != state.get("previous_req_hash", ""):
        _pip_install()
    _restart_services()
    if not _healthy():
        raise UpdateError(
            "rollback completed but the health check still fails — "
            "inspect data/logs/simon.err.log")
    print(f"rollback complete — Simon is on {target} and healthy.")
