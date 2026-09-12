"""Microsoft Teams interface for Simon (botbuilder-core, aiohttp).

Unlike Slack Socket Mode, the Bot Framework requires a **publicly
reachable HTTPS endpoint** for ``POST /api/messages``. For a self-hosted
install this typically means Tailscale Funnel, a Caddy reverse proxy with
valid TLS, or an Azure VM — register the endpoint URL on your Azure Bot
resource. This is expected for production deployments.

Security: if teams_allowed_user_ids is empty, we log a warning and refuse
messages from everyone. Otherwise only those AAD object IDs / Teams user
IDs may interact.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
from pathlib import Path

from aiohttp import web

from simon.agent import Agent
from simon.voice import tts

log = logging.getLogger(__name__)

INTRO = (
    "Good day. Simon at your service — your personal assistant, sir. "
    "Send me a message and I shall attend to it directly."
)
APOLOGY = (
    "I do apologise, sir — something went rather wrong on my end. "
    "Perhaps we might try that again?"
)

TTS_DIR = Path("./data/tts")


class _TeamsSimon:
    """Holds per-conversation Agent instances and the allowlist."""

    def __init__(self, settings):
        self.settings = settings
        raw = (settings.teams_allowed_user_ids or "").strip()
        self.allowed: set[str] = set()
        if raw:
            for part in raw.split(","):
                part = part.strip()
                if part:
                    self.allowed.add(part)
        if not self.allowed:
            log.warning(
                "teams_allowed_user_ids is EMPTY — refusing all messages. "
                "Set TEAMS_ALLOWED_USER_IDS in .env."
            )
        self.agents: dict[str, Agent] = {}

    def is_allowed(self, user_id: str | None) -> bool:
        return bool(user_id) and user_id in self.allowed

    def agent_for(self, session_id: str) -> Agent:
        if session_id not in self.agents:
            self.agents[session_id] = Agent(self.settings, session_id=session_id)
        return self.agents[session_id]


def _strip_mention(activity) -> str:
    """Remove the bot @mention (``<at>Simon</at>``) from activity text."""
    text = activity.text or ""
    try:
        for entity in activity.entities or []:
            if getattr(entity, "type", "") == "mention":
                mentioned = getattr(entity, "mentioned", None)
                name = getattr(mentioned, "name", None)
                if name:
                    text = text.replace(f"<at>{name}</at>", "")
    except Exception:  # noqa: BLE001
        pass
    return text.strip()


def _stage_tts_file(mp3_path: str) -> str | None:
    """Copy the mp3 into ./data/tts under an unguessable name; return name.

    The /api/ttsfile route is tokenless, so the random filename is the only
    protection — acceptable for a short-lived voice note, but only expose it
    when the host is intentionally public (SIMON_PUBLIC_BASE_URL set).
    """
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_urlsafe(24)}.mp3"
    try:
        shutil.copyfile(mp3_path, TTS_DIR / name)
        return name
    except Exception:  # noqa: BLE001
        log.exception("Failed to stage TTS file")
        return None


def create_teams_app(settings) -> web.Application:
    """Build the aiohttp Application exposing POST /api/messages."""
    from botbuilder.core import (
        ActivityHandler,
        BotFrameworkAdapter,
        BotFrameworkAdapterSettings,
        TurnContext,
    )
    from botbuilder.schema import Activity, ActivityTypes, Attachment

    state = _TeamsSimon(settings)
    adapter = BotFrameworkAdapter(
        BotFrameworkAdapterSettings(settings.teams_app_id,
                                    settings.teams_app_password)
    )

    async def _on_error(context: TurnContext, error: Exception) -> None:
        log.exception("Teams bot error", exc_info=error)
        await context.send_activity(APOLOGY)

    adapter.on_turn_error = _on_error

    class SimonTeamsBot(ActivityHandler):
        """Routes message activities through the Agent."""

        async def on_message_activity(self, turn_context: TurnContext) -> None:
            user_id = getattr(turn_context.activity.from_property, "id", None)
            if not state.is_allowed(user_id):
                log.warning("Refused message from Teams user %s", user_id or "?")
                return
            text = _strip_mention(turn_context.activity)
            if not text:
                await turn_context.send_activity(INTRO)
                return
            session_id = str(turn_context.activity.conversation.id)
            try:
                agent = state.agent_for(session_id)
                reply = await asyncio.to_thread(agent.handle, text)
            except Exception:  # noqa: BLE001
                log.exception("Agent error in conversation %s", session_id)
                await turn_context.send_activity(APOLOGY)
                return

            attachments: list[Attachment] = []
            base_url = (settings.simon_public_base_url or "").rstrip("/")
            if base_url:
                try:
                    mp3_path = await asyncio.to_thread(tts.say, reply, settings)
                    name = _stage_tts_file(mp3_path)
                    Path(mp3_path).unlink(missing_ok=True)
                    if name:
                        url = f"{base_url}/api/ttsfile/{name}"
                        attachments.append(Attachment(
                            content_type="audio/mpeg",
                            content_url=url,
                            name="Simon",
                        ))
                except Exception:  # noqa: BLE001
                    log.exception("TTS failed; sending text-only reply")
            else:
                log.debug("SIMON_PUBLIC_BASE_URL unset; text-only reply.")
            await turn_context.send_activity(
                Activity(type=ActivityTypes.message, text=reply,
                         attachments=attachments or None)
            )

        async def on_members_added_activity(self, members_added, turn_context):
            for member in members_added:
                if member.id != turn_context.activity.recipient.id:
                    await turn_context.send_activity(INTRO)

    bot = SimonTeamsBot()

    async def messages(req: web.Request) -> web.Response:
        if "application/json" not in req.headers.get("Content-Type", ""):
            return web.Response(status=415)
        body = await req.json()
        activity = Activity().deserialize(body)
        auth_header = req.headers.get("Authorization", "")
        response = await adapter.process_activity(activity, auth_header,
                                                  bot.on_turn)
        if response:
            return web.json_response(data=response.body, status=response.status)
        return web.Response(status=201)

    async def tts_file(req: web.Request) -> web.StreamResponse:
        name = req.match_info["name"]
        # Filenames we generate are urlsafe tokens + ".mp3"; refuse anything else.
        if not name.endswith(".mp3") or "/" in name or ".." in name:
            return web.Response(status=404)
        path = TTS_DIR / name
        if not path.is_file():
            return web.Response(status=404)
        return web.FileResponse(path, headers={"Content-Type": "audio/mpeg"})

    app = web.Application()
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/api/ttsfile/{name}", tts_file)
    log.info("Simon Teams bot built.")
    return app


def run_teams(settings) -> None:
    """Run the Teams bot over aiohttp (blocking) on TEAMS_PORT."""
    if not settings.teams_app_id:
        raise RuntimeError("TEAMS_APP_ID is not configured.")
    log.info("Starting Simon Teams bot on port %s...", settings.teams_port)
    web.run_app(create_teams_app(settings), host="0.0.0.0",
                port=settings.teams_port)


async def run_teams_async(settings) -> None:
    """Run the Teams bot inside an existing asyncio loop (for run.py 'all').

    Runs until the surrounding task is cancelled.
    """
    if not settings.teams_app_id:
        log.warning("TEAMS_APP_ID not set; Teams interface disabled.")
        return
    runner = web.AppRunner(create_teams_app(settings))
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.teams_port)
    await site.start()
    log.info("Simon Teams bot listening on port %s (async mode).",
             settings.teams_port)
    try:
        await asyncio.Event().wait()  # until cancelled
    finally:
        await runner.cleanup()
