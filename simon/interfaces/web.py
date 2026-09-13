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


def _chunk_words(text: str, size: int = 4):
    """Yield groups of words for a pleasant streaming effect."""
    words = text.split(" ")
    for i in range(0, len(words), size):
        yield " ".join(words[i:i + size]) + (" " if i + size < len(words) else "")


def create_app(settings) -> FastAPI:
    app = FastAPI(title="Simon")
    agents: dict[str, Agent] = {}

    def agent_for(session_id: str) -> Agent:
        if session_id not in agents:
            agents[session_id] = Agent(settings, session_id=session_id,
                                       interface="web")
        return agents[session_id]

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

    app.mount(
        "/static", StaticFiles(directory=STATIC_DIR), name="static"
    )

    # Settings page + API (SPEC.md "Settings Surface").
    try:
        from simon import settings_api
        settings_api.register(app, settings)
    except Exception:  # noqa: BLE001
        log.exception("settings API unavailable")
    return app
