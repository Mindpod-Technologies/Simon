"""Slack interface for Simon (slack-bolt, Socket Mode).

Socket Mode means no public HTTP endpoint is required — the bot opens a
WebSocket to Slack — which makes it ideal for self-hosted installs behind
NAT (Mac Mini, VPS without open ports).

Security: if slack_allowed_user_ids is empty, we log a warning and refuse
messages from everyone. Otherwise only those Slack user IDs may interact.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from simon.agent import Agent
from simon.voice import tts

log = logging.getLogger(__name__)

INTRO = (
    "Good day. Simon at your service — your personal assistant, sir. "
    "DM me or @-mention me in a channel and I shall attend to it directly."
)
APOLOGY = (
    "I do apologise, sir — something went rather wrong on my end. "
    "Perhaps we might try that again?"
)


class _SlackSimon:
    """Holds per-channel/user Agent instances and the allowlist."""

    def __init__(self, settings):
        self.settings = settings
        raw = (settings.slack_allowed_user_ids or "").strip()
        self.allowed: set[str] = set()
        if raw:
            for part in raw.split(","):
                part = part.strip()
                if part:
                    self.allowed.add(part)
        if not self.allowed:
            log.warning(
                "slack_allowed_user_ids is EMPTY — refusing all messages. "
                "Set SLACK_ALLOWED_USER_IDS in .env."
            )
        self.agents: dict[str, Agent] = {}

    def is_allowed(self, user_id: str | None) -> bool:
        return bool(user_id) and user_id in self.allowed

    def agent_for(self, session_id: str) -> Agent:
        if session_id not in self.agents:
            self.agents[session_id] = Agent(self.settings,
                                            session_id=session_id,
                                            interface="slack")
        return self.agents[session_id]


def _strip_mention(text: str) -> str:
    """Remove @bot mentions (``<@U12345>``) from message text."""
    return re.sub(r"<@[A-Z0-9]+>", "", text or "").strip()


def _session_id_for(event: dict, settings=None) -> str:
    """DMs key on the user; channel messages key on the channel.

    When an identity map is configured, a DM user's ID is translated to their
    canonical cross-interface session (e.g. "owner")."""
    if event.get("channel_type") == "im":
        raw = str(event.get("user") or event.get("channel"))
        if settings is not None:
            return settings.canonical_session("slack", raw)
        return raw
    return str(event.get("channel"))


async def _reply_with_voice(say, reply_text: str, settings, channel: str,
                            thread_ts: str | None, client) -> None:
    """Send the text reply, then upload a TTS voice note mp3."""
    await say(text=reply_text, channel=channel, thread_ts=thread_ts)
    try:
        mp3_path = await asyncio.to_thread(tts.say, reply_text, settings)
    except Exception:  # noqa: BLE001
        log.exception("TTS failed")
        await say(
            text="(My voice synthesiser seems to be on the blink, sir — "
                 "text only, I'm afraid.)",
            channel=channel,
            thread_ts=thread_ts,
        )
        return
    try:
        await asyncio.to_thread(
            client.files_upload_v2,
            channel=channel,
            thread_ts=thread_ts,
            file=mp3_path,
            title="Simon",
            filename="simon.mp3",
        )
    except Exception:  # noqa: BLE001
        log.exception("Failed to upload voice reply")
    finally:
        try:
            Path(mp3_path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _build_app(settings):
    """Build the slack_bolt AsyncApp with all handlers registered."""
    from slack_bolt.async_app import AsyncApp

    state = _SlackSimon(settings)
    app = AsyncApp(token=settings.slack_bot_token)

    async def _handle_event(event, say, client) -> None:
        user_id = event.get("user")
        if not state.is_allowed(user_id):
            log.warning("Refused message from Slack user %s", user_id or "?")
            return
        text = _strip_mention(event.get("text", ""))
        channel = event.get("channel")
        thread_ts = event.get("ts")
        if not text:
            await say(text=INTRO, channel=channel, thread_ts=thread_ts)
            return
        session_id = _session_id_for(event, state.settings)
        try:
            from simon import onboarding
            if onboarding.mark(session_id, "slack"):
                log.info("slack session %s onboarded", session_id)
        except Exception:  # noqa: BLE001 - never break a turn
            log.exception("onboarding mark failed")
        try:
            agent = state.agent_for(session_id)
            reply = await asyncio.to_thread(agent.handle, text)
        except Exception:  # noqa: BLE001
            log.exception("Agent error in session %s", session_id)
            await say(text=APOLOGY, channel=channel, thread_ts=thread_ts)
            return
        await _reply_with_voice(say, reply, settings, channel, thread_ts, client)

    @app.event("app_mention")
    async def on_app_mention(event, say, client):
        await _handle_event(event, say, client)

    @app.event("message")
    async def on_message(event, say, client):
        # Only DMs; channel traffic requires an @-mention (app_mention above).
        if event.get("channel_type") != "im":
            return
        if event.get("subtype") or event.get("bot_id"):
            return  # ignore edits/joins and other bots
        await _handle_event(event, say, client)

    log.info("Simon Slack bot built.")
    return app


def run_slack(settings) -> None:
    """Run the Slack bot over Socket Mode (blocking)."""
    if not settings.slack_bot_token or not settings.slack_app_token:
        raise RuntimeError(
            "SLACK_BOT_TOKEN / SLACK_APP_TOKEN are not configured."
        )
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    log.info("Starting Simon Slack bot (Socket Mode)...")
    app = _build_app(settings)
    SocketModeHandler(app, settings.slack_app_token).start()


async def run_slack_async(settings) -> None:
    """Run the Slack bot inside an existing asyncio loop (for run.py 'all').

    Runs until the surrounding task is cancelled.
    """
    if not settings.slack_bot_token or not settings.slack_app_token:
        log.warning(
            "SLACK_BOT_TOKEN/SLACK_APP_TOKEN not set; Slack interface disabled."
        )
        return
    from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler

    app = _build_app(settings)
    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    await handler.start_async()
    log.info("Simon Slack bot connected via Socket Mode (async mode).")
    try:
        await asyncio.Event().wait()  # until cancelled
    finally:
        await handler.close_async()
