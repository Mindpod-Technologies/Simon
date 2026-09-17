"""Web interface for Simon: FastAPI app with SSE chat, TTS and STT endpoints."""
from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from simon.agent import Agent
from simon.voice import stt, tts

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parents[2] / "web" / "static"

SESSION_COOKIE = "simon_session"


class ChatRequest(BaseModel):
    text: str
    session_id: str | None = None


class TTSRequest(BaseModel):
    text: str


class TaskRequest(BaseModel):
    name: str


class FactRequest(BaseModel):
    key: str
    value: str


class LoginRequest(BaseModel):
    password: str


def _chunk_words(text: str, size: int = 4):
    """Yield groups of words for a pleasant streaming effect."""
    words = text.split(" ")
    for i in range(0, len(words), size):
        yield " ".join(words[i:i + size]) + (" " if i + size < len(words) else "")


def create_app(settings) -> FastAPI:
    app = FastAPI(title="Simon")
    agents: dict[str, Agent] = {}

    # ---- owner auth gate ----
    from simon import auth as auth_mod

    def live_password_hash() -> str:
        """Read the hash live from .env so completing /setup takes effect
        without a restart (the cached Settings would lag until then)."""
        try:
            from simon import settings_api
            values, _ = settings_api.read_env()
            return values.get("SIMON_OWNER_PASSWORD_HASH", "")
        except Exception:  # noqa: BLE001
            return auth_mod.password_hash(settings)

    @app.middleware("http")
    async def auth_gate(request: Request, call_next):
        path = request.url.path
        stored = live_password_hash()
        first_run = not stored
        if auth_mod.is_public_path(path, first_run=first_run):
            return await call_next(request)
        token = request.cookies.get(auth_mod.COOKIE_NAME, "")
        if auth_mod.check_session(token, stored):
            return await call_next(request)
        if path.startswith("/api/"):
            payload = {"error": "authentication required"}
            if first_run:
                payload["setup_required"] = True
            return JSONResponse(payload, status_code=401)
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/setup" if first_run else "/login",
                                status_code=303)

    @app.get("/login")
    async def login_page():
        return FileResponse(STATIC_DIR / "login.html")

    @app.get("/quickask")
    async def quickask_page():
        """Compact ask-anything page — loaded by the desktop tray popup."""
        return FileResponse(STATIC_DIR / "quickask.html")

    @app.post("/api/login")
    async def login(req: LoginRequest):
        from fastapi.responses import Response
        if auth_mod.login_throttled():
            return JSONResponse(
                {"error": "too many attempts — wait a minute"}, status_code=429)
        stored = live_password_hash()
        if not stored or not auth_mod.verify_password(req.password, stored):
            auth_mod.record_login(False)
            return JSONResponse({"error": "wrong password"}, status_code=401)
        auth_mod.record_login(True)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            auth_mod.COOKIE_NAME, auth_mod.make_session(stored),
            max_age=7 * 24 * 3600, httponly=True, samesite="lax")
        return response

    @app.post("/api/logout")
    async def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie(auth_mod.COOKIE_NAME)
        return response

    def agent_for(session_id: str) -> Agent:
        if session_id not in agents:
            from simon import tasks as tasks_mod
            agent_settings = settings
            workspace = tasks_mod.session_workspace(session_id, settings)
            if workspace is not None:
                # Task sessions get their own workspace directory — files
                # Simon reads/writes stay scoped to that project.
                agent_settings = settings.model_copy(
                    update={"simon_workspace_dir": str(workspace)})
            agents[session_id] = Agent(agent_settings, session_id=session_id,
                                       interface="web")
        return agents[session_id]

    def settings_for_session(session_id: str | None):
        """Settings whose workspace matches the session (task-aware)."""
        if session_id:
            from simon import tasks as tasks_mod
            workspace = tasks_mod.session_workspace(session_id, settings)
            if workspace is not None:
                return settings.model_copy(
                    update={"simon_workspace_dir": str(workspace)})
        return settings

    def session_for(request: Request, supplied: str | None) -> str:
        if supplied:
            return settings.canonical_session("web", supplied)
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie:
            return settings.canonical_session("web", cookie)
        # Anonymous web visitor: land on the configured default session (e.g.
        # the owner's unified session) when set, else a fresh random one.
        return settings.simon_web_default_session or uuid.uuid4().hex

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.post("/api/chat")
    async def chat(req: ChatRequest, request: Request):
        session_id = session_for(request, req.session_id)

        async def stream():
            try:
                # Run the blocking agent loop off the event loop.
                reply = await asyncio.to_thread(
                    agent_for(session_id).handle, req.text
                )
            except Exception:  # noqa: BLE001
                log.exception("Agent error (session %s)", session_id)
                reply = (
                    "I do apologise, sir — something went rather wrong "
                    "on my end."
                )
            for chunk in _chunk_words(reply):
                yield {"event": "chunk", "data": chunk}
                await asyncio.sleep(0.03)
            yield {"event": "done", "data": reply}
            yield {"event": "session", "data": session_id}

        return EventSourceResponse(stream())

    @app.post("/api/tts")
    async def tts_endpoint(req: TTSRequest):
        try:
            mp3_path = await asyncio.to_thread(tts.say, req.text, settings)
        except Exception:  # noqa: BLE001
            log.exception("TTS failed")
            return JSONResponse({"error": "TTS failed"}, status_code=500)
        return FileResponse(mp3_path, media_type="audio/mpeg")

    @app.post("/api/stt")
    async def stt_endpoint(file: UploadFile):
        suffix = Path(file.filename or "audio.webm").suffix or ".webm"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            tmp.write(await file.read())
            tmp.close()
            text = await asyncio.to_thread(stt.transcribe, tmp.name, settings)
        except Exception:  # noqa: BLE001
            log.exception("STT failed")
            return JSONResponse({"error": "STT failed"}, status_code=500)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        return {"text": text}

    # ---- documents: upload for review, download what Simon creates ----

    @app.post("/api/upload")
    async def upload(file: UploadFile):
        """Store an uploaded document and ingest its text into RAG memory."""
        from simon import docs
        try:
            data = await file.read()
            if len(data) > 25 * 1024 * 1024:
                return JSONResponse({"error": "file too large (25 MB max)"},
                                    status_code=413)
            info = await asyncio.to_thread(
                docs.save_upload, data, file.filename or "upload", settings)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:  # noqa: BLE001
            log.exception("upload failed")
            return JSONResponse({"error": "upload failed"}, status_code=500)
        return info

    @app.get("/api/uploads")
    async def uploads():
        from simon import docs
        return {"files": docs.list_uploads(settings)}

    @app.get("/api/documents")
    async def documents():
        from simon import docs
        return {"files": docs.list_documents(settings)}

    @app.get("/api/documents/{name}")
    async def download_document(name: str):
        from simon import docs
        path = docs.resolve_document(settings, name)
        if path is None:
            return JSONResponse({"error": "no such document"},
                                status_code=404)
        return FileResponse(path, filename=path.name)

    # ---- activity: jobs + recurring schedules ----

    @app.get("/api/jobs")
    async def jobs_endpoint():
        from simon import jobs as jobs_mod
        rows = jobs_mod.list_jobs(limit=6)
        return {"jobs": [
            {"id": j["id"], "status": j["status"],
             "description": j["description"][:90],
             "created_at": j["created_at"]}
            for j in rows]}

    @app.get("/api/schedules")
    async def schedules_endpoint():
        from simon import schedules as sched_mod
        rows = sched_mod.list_schedules(active_only=True)
        return {"schedules": [
            {"id": r["id"], "description": r["description"][:90],
             "when": sched_mod.describe(r)}
            for r in rows]}

    # ---- artifacts: workspace files Simon creates (charts, docs, code) ----

    @app.get("/api/artifacts")
    async def artifacts_list(session_id: str | None = None):
        from simon import artifacts as art_mod
        return {"files": art_mod.list_artifacts(
            settings_for_session(session_id))}

    @app.get("/api/artifacts/{relpath:path}")
    async def artifact_fetch(relpath: str, session_id: str | None = None,
                             download: bool = False):
        from simon import artifacts as art_mod
        path = art_mod.resolve_artifact(settings_for_session(session_id),
                                        relpath)
        if path is None:
            return JSONResponse({"error": "no such artifact"},
                                status_code=404)
        media = art_mod.media_type_for(path)
        if download:
            return FileResponse(path, filename=path.name)
        return FileResponse(path, media_type=media)

    # ---- tasks: named projects with their own workspace + session ----

    @app.get("/api/tasks")
    async def tasks_list():
        from simon import tasks as tasks_mod
        return {"tasks": tasks_mod.list_tasks(settings)}

    @app.post("/api/tasks")
    async def task_create(req: TaskRequest):
        from simon import tasks as tasks_mod
        try:
            task = tasks_mod.create_task(req.name, settings)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return task

    @app.delete("/api/tasks/{slug}")
    async def task_delete(slug: str):
        from simon import tasks as tasks_mod
        if not tasks_mod.delete_task(slug, settings):
            return JSONResponse({"error": "no such task"}, status_code=404)
        agents.pop(tasks_mod.session_for(slug), None)
        return {"deleted": slug}

    # ---- memory: browse/edit Simon's long-term facts ----

    @app.get("/api/history")
    async def history(request: Request, session_id: str | None = None):
        from simon import memory
        sid = session_for(request, session_id)
        return {"session_id": sid,
                "messages": memory.get_history(sid, limit=60)}

    @app.get("/api/facts")
    async def facts_list():
        from simon import memory
        return {"facts": memory.list_facts()}

    @app.post("/api/facts")
    async def fact_save(req: FactRequest):
        from simon import memory
        key = req.key.strip().lower().replace(" ", "_")
        if not key or not req.value.strip():
            return JSONResponse({"error": "key and value are required"},
                                status_code=400)
        memory.set_fact(key, req.value.strip())
        return {"saved": key}

    @app.delete("/api/facts/{key}")
    async def fact_delete(key: str):
        from simon import memory
        if not memory.delete_fact(key):
            return JSONResponse({"error": "no such fact"}, status_code=404)
        return {"deleted": key}

    @app.get("/api/license")
    async def license_status():
        """Current commercial license status (plan, email, expiry, reason)."""
        from dataclasses import asdict
        from simon import licensing
        return asdict(licensing.check_license(settings))

    @app.get("/api/plugins")
    async def plugins_status():
        """Loaded drop-in plugins (+ their tools) and configured MCP servers."""
        import json as _json
        from simon.tools import LOADED_PLUGINS, ToolRegistry, load_plugins
        if not LOADED_PLUGINS:
            # No agent has been built yet this process — probe-load the
            # plugins into a throwaway registry so the panel isn't empty.
            try:
                load_plugins(ToolRegistry())
            except Exception:  # noqa: BLE001
                pass
        mcp_names: list[str] = []
        cfg = getattr(settings, "simon_mcp_config", "") or "mcp.json"
        try:
            data = _json.loads((Path(cfg)).read_text())
            # Server NAMES only — the config may carry tokens as values.
            # Accept both conventions: Claude-Desktop "mcpServers" and the
            # shorter "servers" used by mcp_client.
            servers = data.get("mcpServers") or data.get("servers") or {}
            mcp_names = sorted(servers.keys())
        except Exception:  # noqa: BLE001
            pass
        return {
            "plugins": [{"name": n, "tools": t}
                        for n, t in sorted(LOADED_PLUGINS.items())],
            "mcp_servers": mcp_names,
        }

    @app.get("/api/approvals")
    async def approvals_status():
        """Pending sensitive-action approvals awaiting the owner's decision —
        the desktop tray polls this for its ❗ badge and alerts."""
        from simon import approvals
        return {"pending": [
            {"id": p["id"], "summary": p["summary"],
             "session": p["session_id"], "created_at": p["created_at"]}
            for p in approvals.list_pending()]}

    @app.get("/api/profiles")
    async def profiles_list():
        """Known per-person profiles (session → display name)."""
        from simon import memory as _m, profiles as _p
        _p.init_db()
        conn = _m._connect()
        try:
            rows = conn.execute(
                "SELECT session_id, name, form, updated_at FROM profiles"
                " ORDER BY updated_at DESC").fetchall()
            return {"profiles": [dict(r) for r in rows]}
        finally:
            conn.close()

    @app.post("/api/profiles")
    async def profiles_set(request: Request):
        """Set a display name for a session, e.g. name the wife's chat."""
        from simon import profiles as _p
        body = await request.json()
        session = str(body.get("session", "")).strip()
        name = str(body.get("name", "")).strip()
        form = str(body.get("form", "")).strip()
        if not session or not name:
            return JSONResponse(
                {"error": "session and name are required"}, status_code=400)
        _p.set_profile(session, name, form)
        return {"saved": {"session": session, "name": name, "form": form}}

    app.mount(
        "/static", StaticFiles(directory=STATIC_DIR), name="static"
    )

    # Settings page + API (SPEC.md "Settings Surface").
    try:
        from simon import settings_api
        settings_api.register(app, settings)
    except Exception:  # noqa: BLE001
        log.exception("settings API unavailable")
    try:
        from simon import setup_api
        setup_api.register(app, settings)
    except Exception:  # noqa: BLE001
        log.exception("setup API unavailable")
    return app
