"""First-run onboarding: /setup wizard backend.

Walks a fresh install through: owner password → brain (Ollama models +
optional frontier key) → channels (Telegram/Slack/Teams) → mailbox. Writes
go through settings_api.write_env so .env keeps its comments and ordering.

Security: the save/pull endpoints are reachable WITHOUT a session only
during first-run (no password set); web.py's auth gate enforces that.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import urllib.request

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import settings_api

log = logging.getLogger(__name__)

STATIC_DIR = settings_api.STATIC_DIR

# Keys the wizard may write. Deliberately narrower than the raw editor.
SETUP_KEYS = {
    "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_MODEL_FAST",
    "LLM_ROUTER_ENABLED",
    "LLM_FRONTIER_BASE_URL", "LLM_FRONTIER_MODEL", "LLM_FRONTIER_API_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS",
    "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_ALLOWED_USER_IDS",
    "TEAMS_APP_ID", "TEAMS_APP_PASSWORD", "TEAMS_ALLOWED_USER_IDS",
    "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
    "SIMON_MAILBOX",
}

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Model pull state (in-memory; one pull at a time is plenty).
_pull_lock = threading.Lock()
_pull_state = {"running": False, "model": "", "log": "", "ok": None}


def _ollama_models() -> list[str]:
    try:
        with urllib.request.urlopen(
                "http://localhost:11434/api/tags", timeout=3) as resp:
            import json as _json
            data = _json.loads(resp.read())
        return sorted(m["name"] for m in data.get("models", []))
    except Exception:
        return []


def _pull_worker(model: str) -> None:
    try:
        proc = subprocess.run(
            ["ollama", "pull", model], capture_output=True, text=True,
            timeout=1800)
        with _pull_lock:
            _pull_state["running"] = False
            _pull_state["ok"] = proc.returncode == 0
            _pull_state["log"] = (proc.stdout + proc.stderr)[-2000:]
    except Exception as exc:  # noqa: BLE001
        with _pull_lock:
            _pull_state["running"] = False
            _pull_state["ok"] = False
            _pull_state["log"] = str(exc)[:500]


class SaveRequest(BaseModel):
    password: str = ""
    updates: dict[str, str] = {}


class PullRequest(BaseModel):
    model: str


def register(app: FastAPI, settings) -> None:
    @app.get("/setup")
    async def setup_page():
        return FileResponse(STATIC_DIR / "setup.html")

    @app.get("/api/setup/status")
    async def setup_status():
        values, _ = settings_api.read_env()
        return {
            "password_set": bool(values.get("SIMON_OWNER_PASSWORD_HASH")),
            "ollama_running": bool(_ollama_models() or _ollama_up()),
            "ollama_models": _ollama_models(),
            "current": {k: settings_api.mask(k, values.get(k, ""))
                        for k in sorted(SETUP_KEYS)},
        }

    @app.post("/api/setup/save")
    async def setup_save(req: SaveRequest):
        from . import auth as auth_mod
        updates = dict(req.updates or {})
        unknown = [k for k in updates
                   if k not in SETUP_KEYS and k != "SIMON_OWNER_PASSWORD_HASH"]
        if unknown:
            return JSONResponse(
                {"error": f"not allowed here: {', '.join(unknown)}"},
                status_code=400)
        values, _ = settings_api.read_env()
        first_run = not values.get("SIMON_OWNER_PASSWORD_HASH")
        if req.password:
            if len(req.password) < 8:
                return JSONResponse(
                    {"error": "password must be at least 8 characters"},
                    status_code=400)
            if not first_run:
                return JSONResponse(
                    {"error": "password is already set — use Settings"},
                    status_code=400)
            updates["SIMON_OWNER_PASSWORD_HASH"] = \
                auth_mod.hash_password(req.password)
        bad = [k for k in updates if not _KEY_RE.match(k)]
        if bad:
            return JSONResponse({"error": f"invalid key: {bad[0]}"},
                                status_code=400)
        try:
            changed = settings_api.write_env(updates)
        except Exception as exc:  # noqa: BLE001
            log.exception("setup save failed")
            return JSONResponse({"error": str(exc)}, status_code=500)
        return {"saved": changed, "restart_required": True,
                "password_set": "SIMON_OWNER_PASSWORD_HASH" in changed}

    @app.post("/api/setup/pull_model")
    async def setup_pull(req: PullRequest):
        model = (req.model or "").strip()
        if not re.match(r"^[\w.\-/:]+$", model):
            return JSONResponse({"error": "invalid model name"},
                                status_code=400)
        with _pull_lock:
            if _pull_state["running"]:
                return JSONResponse(
                    {"error": f"already pulling {_pull_state['model']}"},
                    status_code=409)
            _pull_state.update({"running": True, "model": model,
                                "log": "", "ok": None})
        threading.Thread(target=_pull_worker, args=(model,),
                         daemon=True).start()
        return {"pulling": model}

    @app.get("/api/setup/pull_status")
    async def setup_pull_status():
        with _pull_lock:
            return dict(_pull_state)


def _ollama_up() -> bool:
    try:
        with urllib.request.urlopen("http://localhost:11434/",
                                    timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False
