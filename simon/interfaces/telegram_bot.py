"""Telegram interface for Simon (python-telegram-bot v21, long polling).

Security: if telegram_allowed_user_ids is empty, we log a warning and refuse
messages from everyone. Otherwise only those user IDs may interact.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Callable
import subprocess
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from simon.agent import Agent
from simon.voice import stt, tts

log = logging.getLogger(__name__)

INTRO = (
    "Good day. Simon at your service — your personal assistant, sir. "
    "Send me a message or a voice note and I shall attend to it directly."
)
APOLOGY = (
    "I do apologise, sir — something went rather wrong on my end. "
    "Perhaps we might try that again?"
)


class _TelegramSimon:
    """Holds per-chat Agent instances and the allowlist."""

    def __init__(self, settings):
        self.settings = settings
        raw = (settings.telegram_allowed_user_ids or "").strip()
        self.allowed: set[int] = set()
        if raw:
            for part in raw.split(","):
                part = part.strip()
                if part:
                    try:
                        self.allowed.add(int(part))
                    except ValueError:
                        log.warning("Ignoring invalid Telegram user id: %r", part)
        if not self.allowed:
            log.warning(
                "telegram_allowed_user_ids is EMPTY — refusing all messages. "
                "Set TELEGRAM_ALLOWED_USER_IDS in .env."
            )
        self.agents: dict[int, Agent] = {}

    def is_allowed(self, update: Update) -> bool:
        user = update.effective_user
        return bool(user) and user.id in self.allowed

    def agent_for(self, chat_id: int) -> Agent:
        session = self.settings.canonical_session("telegram", str(chat_id))
        if session not in self.agents:
            self.agents[session] = Agent(
                self.settings, session_id=session, interface="telegram"
            )
        return self.agents[session]


def _mp3_to_ogg(mp3_path: str) -> str | None:
    """Convert mp3 to ogg/opus for a proper Telegram voice note.

    Returns ogg path, or None if ffmpeg is unavailable/failed.
    """
    if not shutil.which("ffmpeg"):
        return None
    ogg_path = str(Path(mp3_path).with_suffix(".ogg"))
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", mp3_path,
                "-c:a", "libopus", "-b:a", "48k", ogg_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return ogg_path
    except Exception as exc:  # noqa: BLE001
        log.warning("ffmpeg conversion failed: %s", exc)
        return None


async def _reply_with_voice(message, reply_text: str, settings) -> None:
    """Send the text reply plus a voice note (ogg if possible, else mp3)."""
    await message.reply_text(reply_text)
    try:
        mp3_path = tts.say(reply_text, settings)
    except Exception:  # noqa: BLE001
        log.exception("TTS failed")
        await message.reply_text(
            "(My voice synthesiser seems to be on the blink, sir — "
            "text only, I'm afraid.)"
        )
        return
    ogg_path = _mp3_to_ogg(mp3_path)
    try:
        if ogg_path:
            with open(ogg_path, "rb") as fh:
                await message.reply_voice(voice=fh)
        else:
            with open(mp3_path, "rb") as fh:
                await message.reply_audio(audio=fh, title="Simon")
    except Exception:  # noqa: BLE001
        log.exception("Failed to send voice reply")
    finally:
        for p in (ogg_path, mp3_path):
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass


def _build_app(settings) -> Application:
    """Build the PTB Application with all handlers registered."""
    state = _TelegramSimon(settings)

    async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not state.is_allowed(update):
            log.warning(
                "Refused /start from user %s",
                update.effective_user.id if update.effective_user else "?",
            )
            return
        await update.message.reply_text(INTRO)

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not state.is_allowed(update):
            log.warning(
                "Refused message from user %s",
                update.effective_user.id if update.effective_user else "?",
            )
            return
        chat_id = update.effective_chat.id
        try:
            agent = state.agent_for(chat_id)
            reply = agent.handle(update.message.text)
        except Exception:  # noqa: BLE001
            log.exception("Agent error in chat %s", chat_id)
            await update.message.reply_text(APOLOGY)
            return
        await _reply_with_voice(update.message, reply, settings)

    async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not state.is_allowed(update):
            log.warning(
                "Refused voice message from user %s",
                update.effective_user.id if update.effective_user else "?",
            )
            return
        chat_id = update.effective_chat.id
        voice = update.message.voice or update.message.audio
        if voice is None:
            return
        tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        tmp.close()
        try:
            tg_file = await context.bot.get_file(voice.file_id)
            await tg_file.download_to_drive(tmp.name)
            text = stt.transcribe(tmp.name, settings)
        except Exception:  # noqa: BLE001
            log.exception("STT failed in chat %s", chat_id)
            await update.message.reply_text(APOLOGY)
            return
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        if not text.strip():
            await update.message.reply_text(
                "I'm terribly sorry sir, I couldn't quite make that out."
            )
            return
        try:
            agent = state.agent_for(chat_id)
            reply = agent.handle(text)
        except Exception:  # noqa: BLE001
            log.exception("Agent error in chat %s", chat_id)
            await update.message.reply_text(APOLOGY)
            return
        await _reply_with_voice(update.message, reply, settings)

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(
        MessageHandler(filters.VOICE | filters.AUDIO, on_voice)
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text)
    )
    log.info("Simon Telegram bot built.")
    return app


def run_telegram(settings) -> None:
    """Run the Telegram bot with long polling (blocking)."""
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    log.info("Starting Simon Telegram bot (long polling)...")
    _build_app(settings).run_polling(allowed_updates=Update.ALL_TYPES)


async def run_telegram_async(settings) -> None:
    """Run the Telegram bot inside an existing asyncio loop (for run.py 'all').

    Runs until the surrounding task is cancelled.
    """
    if not settings.telegram_bot_token:
        log.warning("TELEGRAM_BOT_TOKEN not set; Telegram interface disabled.")
        return
    app = _build_app(settings)
    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        log.info("Simon Telegram bot polling (async mode).")
        try:
            await asyncio.Event().wait()  # until cancelled
        finally:
            await app.updater.stop()
            await app.stop()


def make_notify(settings) -> Callable[[str], None]:
    """Return a sync notify(text) that messages every allowlisted Telegram user.

    Used by the scheduler to deliver briefings/reminders. Must be called from
    a running event loop (AsyncIOScheduler guarantees this).
    """
    from telegram import Bot

    bot = Bot(token=settings.telegram_bot_token)
    ids = [
        int(part.strip())
        for part in settings.telegram_allowed_user_ids.split(",")
        if part.strip().lstrip("-").isdigit()
    ]
    if not ids:
        log.warning("make_notify: no TELEGRAM_ALLOWED_USER_IDS; notifications dropped.")

    def notify(text: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning("notify outside event loop; dropped: %s", text[:80])
            return
        for uid in ids:
            loop.create_task(bot.send_message(chat_id=uid, text=text))

    return notify
