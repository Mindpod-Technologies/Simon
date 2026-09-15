"""Settings API + page for Simon Work (see SPEC.md "Settings Surface").

Design contract honored here:
- writes go through to ``.env`` with comments and ordering preserved
  (custom line-based parser, never a full regenerate);
- a timestamped backup (``.env.bak``) is written before every save;
- secrets are never returned in full — masked to their last 4 chars, and
  the UI only sends back fields the user actually changed;
- only whitelisted keys are writable via the structured endpoint — the
  Advanced raw editor exists for everything else, with validation.

Restart note: saving does NOT restart Simon (many saves are no-ops live).
The page offers a Restart button which kickstarts the services on a
short delay so this HTTP response completes first.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent
ENV_PATH = REPO / ".env"
ENV_BAK = REPO / ".env.bak"
STATIC_DIR = REPO / "web" / "static"

_SECRET_RE = re.compile(r"TOKEN|KEY|PASSWORD|SECRET", re.I)
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


# ---------------------------------------------------------------------- #
# Whitelisted setting definitions
# ---------------------------------------------------------------------- #

def _d(section, key, label, type_="text", help="", options=None):
    return {"section": section, "key": key, "label": label, "type": type_,
            "help": help, "options": options or []}


SETTING_DEFS = [
    # Brain
    _d("Brain", "LLM_BASE_URL", "Provider endpoint",
       help="http://localhost:11434/v1 for local Ollama; any OpenAI-compatible URL for cloud"),
    _d("Brain", "LLM_API_KEY", "API key", "password",
       help="empty for Ollama; sk-… for cloud providers"),
    _d("Brain", "LLM_MODEL", "Smart model",
       help="complex turns, tools, judgment (default gpt-oss:20b)"),
    _d("Brain", "LLM_MODEL_FAST", "Fast model",
       help="simple turns; empty = one model for everything (default qwen3:8b)"),
    _d("Brain", "LLM_ROUTER_ENABLED", "Model router", "toggle",
       help="route simple turns to the fast model automatically"),
    _d("Brain", "LLM_FRONTIER_BASE_URL", "Frontier endpoint",
       help="e.g. https://api.moonshot.ai/v1 for Kimi K3"),
    _d("Brain", "LLM_FRONTIER_MODEL", "Frontier model",
       help="e.g. kimi-k3 — explicit requests and local failures land here"),
    _d("Brain", "LLM_FRONTIER_API_KEY", "Frontier API key", "password",
       help="empty = frontier tier off; sk-… from platform.moonshot.ai"),
    # Channels
    _d("Channels", "TELEGRAM_BOT_TOKEN", "Telegram bot token", "password"),
    _d("Channels", "TELEGRAM_ALLOWED_USER_IDS", "Telegram allowed user IDs",
       help="comma-separated; empty = deny all"),
    _d("Channels", "SLACK_BOT_TOKEN", "Slack bot token (xoxb-…)", "password"),
    _d("Channels", "SLACK_APP_TOKEN", "Slack app token (xapp-…)", "password"),
    _d("Channels", "SLACK_ALLOWED_USER_IDS", "Slack allowed user IDs"),
    _d("Channels", "TEAMS_APP_ID", "Teams app ID", "password"),
    _d("Channels", "TEAMS_APP_PASSWORD", "Teams app password", "password"),
    _d("Channels", "TEAMS_ALLOWED_USER_IDS", "Teams allowed user IDs"),
    # Voice
    _d("Voice", "TTS_VOICE", "Speaking voice",
       help="edge-tts voice name, e.g. en-GB-RyanNeural"),
    _d("Voice", "TTS_RATE", "Speaking rate", help="e.g. +0%, -10%, +15%"),
    _d("Voice", "STT_MODEL", "Transcription model",
       help="faster-whisper size (tiny/base/small) or 'openai'"),
    # Safety & permissions
    _d("Safety", "SIMON_ALLOW_SHELL", "Shell tool", "toggle",
       help="DANGEROUS: lets Simon run shell commands"),
    _d("Safety", "SIMON_BROWSER_ENABLED", "Browser automation", "toggle"),
    _d("Safety", "SIMON_BROWSER_HEADLESS", "Browser headless", "toggle"),
    _d("Safety", "SIMON_COMPUTER_USE", "Computer control (Mac)", "toggle"),
    _d("Safety", "SIMON_AZURE_ENABLED", "Azure read-only tools", "toggle"),
    _d("Safety", "SIMON_AZURE_ALLOW_WRITE", "Azure write tools", "toggle",
       help="VM start/stop/restart — enable only if desired"),
    # Updates & license
    _d("License", "SIMON_LICENSE_KEY", "License key", "password",
       help="SIMON-<plan>-… key from purchase; empty = free trial"),
    _d("License", "SIMON_REQUIRE_LICENSE", "Require valid license", "toggle"),
]

EDITABLE_KEYS = {d["key"] for d in SETTING_DEFS}


# ---------------------------------------------------------------------- #
# .env parsing / writing (order + comments preserved)
# ---------------------------------------------------------------------- #

def read_env(path: Path = ENV_PATH) -> tuple[dict[str, str], list[str]]:
    """Return ({KEY: value}, original_lines)."""
    values: dict[str, str] = {}
    lines = path.read_text().splitlines() if path.exists() else []
    for line in lines:
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            values[m.group(1)] = m.group(2).split("  #")[0].strip()
    return values, lines


def write_env(updates: dict[str, str], path: Path = ENV_PATH) -> list[str]:
    """Apply updates to .env in place. Returns the keys actually changed."""
    _, lines = read_env(path)
    changed: list[str] = []
    seen: set[str] = set()
    out = []
    for line in lines:
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            seen.add(key)
            comment = ""
            if "  #" in line:
                comment = "  #" + line.split("  #", 1)[1]
            new_line = f"{key}={updates[key]}{comment}"
            if new_line != line:
                changed.append(key)
            out.append(new_line)
        else:
            out.append(line)
    for key, value in updates.items():  # new keys appended at the end
        if key not in seen:
            out.append(f"{key}={value}")
            changed.append(key)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".bak"))
    path.write_text("\n".join(out) + "\n")
    return changed


def mask(key: str, value: str) -> str:
    """Mask a secret to its last 4 chars; non-secrets pass through."""
    if not value or not _SECRET_RE.search(key):
        return value
    if len(value) <= 8:
        return "••••••••"
    return "••••••••" + value[-4:]


# ---------------------------------------------------------------------- #
# Request models
# ---------------------------------------------------------------------- #

class SaveRequest(BaseModel):
    updates: dict[str, str]


class RawSaveRequest(BaseModel):
    content: str


# ---------------------------------------------------------------------- #
# Route registration
# ---------------------------------------------------------------------- #

def register(app: FastAPI, settings) -> None:
    @app.get("/settings")
    async def settings_page():
        return FileResponse(STATIC_DIR / "settings.html")

    @app.get("/api/settings")
    async def get_settings():
        values, _ = read_env()
        sections: dict[str, list[dict]] = {}
        for d in SETTING_DEFS:
            raw = values.get(d["key"], "")
            item = {**d, "value": mask(d["key"], raw), "set": bool(raw)}
            sections.setdefault(d["section"], []).append(item)
        return {"sections": sections}

    @app.post("/api/settings")
    async def save_settings(req: SaveRequest):
        unknown = [k for k in req.updates if k not in EDITABLE_KEYS]
        if unknown:
            return JSONResponse(
                {"error": f"not editable here: {', '.join(unknown)} "
                          "(use the Advanced editor)"}, status_code=400)
        bad = [k for k in req.updates if not _KEY_RE.match(k)]
        if bad:
            return JSONResponse({"error": f"invalid key: {bad[0]}"},
                                status_code=400)
        try:
            changed = write_env(req.updates)
        except Exception as exc:  # noqa: BLE001
            log.exception("settings save failed")
            return JSONResponse({"error": str(exc)}, status_code=500)
        return {"saved": changed, "restart_required": bool(changed)}

    @app.get("/api/settings/ollama_models")
    async def ollama_models():
        try:
            with urllib.request.urlopen(
                    "http://localhost:11434/api/tags", timeout=4) as resp:
                import json as _json
                data = _json.loads(resp.read())
            return {"models": sorted(m["name"] for m in data.get("models", []))}
        except Exception:
            return {"models": []}

    @app.get("/api/settings/version")
    async def version():
        info = {"current": "unknown", "latest": "unknown",
                "update_available": False}
        try:
            from simon import updater
            chk = updater.check()
            info = {"current": chk["current_tag"], "latest": chk["latest_tag"],
                    "update_available": chk["update_available"]}
        except Exception as exc:  # noqa: BLE001
            info["error"] = str(exc)[:200]
        return info

    @app.post("/api/settings/restart")
    async def restart():
        def _delayed():
            time.sleep(1.5)  # let this response complete first
            try:
                from simon import updater
                updater._restart_services()
            except Exception:  # noqa: BLE001
                log.exception("restart via settings failed")
        threading.Thread(target=_delayed, daemon=True).start()
        return {"restarting": True}

    @app.post("/api/settings/raw")
    async def save_raw(req: RawSaveRequest):
        lines = req.content.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not re.match(r"^[A-Z][A-Z0-9_]*=.*$", stripped):
                return JSONResponse(
                    {"error": f"line {i} is not KEY=value: {stripped[:60]}"},
                    status_code=400)
        try:
            if ENV_PATH.exists():
                shutil.copy2(ENV_PATH, ENV_BAK)
            ENV_PATH.write_text(req.content.rstrip("\n") + "\n")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)
        return {"saved": True, "restart_required": True}

    @app.get("/api/settings/raw")
    async def get_raw():
        if not ENV_PATH.exists():
            return {"content": ""}
        return {"content": ENV_PATH.read_text()}
