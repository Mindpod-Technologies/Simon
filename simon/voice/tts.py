"""Text-to-speech via edge-tts (British JARVIS voice by default)."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime

import edge_tts

DEFAULT_VOICE = "en-GB-RyanNeural"
DEFAULT_RATE = "+0%"

# Markdown replies are for the eye, not the ear — edge-tts reads literal
# markup aloud ("asterisk asterisk", "hash", "open bracket"). speech_text()
# rewrites a markdown reply into plain spoken language. Used by synthesize()
# so every channel (web, Slack, Teams, Telegram, voice CLI) benefits.

_FENCED_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_BOLD_RE = re.compile(r"(\*\*|__)(.*?)\1")
_ITALIC_RE = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])|(?<![\w_])_([^_\n]+)_(?![\w_])")
_CODE_RE = re.compile(r"`([^`]*)`")
_HEADING_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_QUOTE_RE = re.compile(r"^>\s?", re.MULTILINE)
_BULLET_RE = re.compile(r"^[ \t]*[-*+]\s+", re.MULTILINE)
_RULE_RE = re.compile(r"^\s*([-*_])\1{2,}\s*$", re.MULTILINE)
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]+\s*$", re.MULTILINE)
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def speech_text(text: str) -> str:
    """Convert markdown-formatted reply text into plain speakable text."""
    if not text:
        return ""
    out = text
    # Fenced code: don't read code aloud — acknowledge it instead.
    out = _FENCED_RE.sub(" I've included a code snippet in the chat. ", out)
    out = _IMAGE_RE.sub(" ", out)                       # images: drop
    out = _LINK_RE.sub(r"\1", out)                      # links: speak label only
    out = _BOLD_RE.sub(r"\2", out)
    out = _ITALIC_RE.sub(lambda m: m.group(1) or m.group(2), out)
    out = _CODE_RE.sub(r"\1", out)                      # inline code: keep words
    out = _HEADING_RE.sub("", out)
    out = _QUOTE_RE.sub("", out)
    out = _BULLET_RE.sub("", out)
    out = _RULE_RE.sub(" ", out)
    out = _TABLE_SEP_RE.sub("", out)                    # markdown table dividers
    out = out.replace("|", ", ")                        # table cells read as lists
    out = _HTML_TAG_RE.sub(" ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


async def synthesize(text: str, out_path: str, voice: str = DEFAULT_VOICE, rate: str = DEFAULT_RATE) -> str:
    """Synthesize ``text`` to an mp3 at ``out_path`` using edge-tts.

    Returns ``out_path``. Raises ValueError on empty text, RuntimeError on
    synthesis failure.
    """
    if not text:
        raise ValueError("cannot synthesize empty text")
    text = speech_text(text)
    if not text.strip():
        raise ValueError("cannot synthesize empty text")
    voice = voice or DEFAULT_VOICE
    rate = rate or DEFAULT_RATE
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    try:
        await communicate.save(out_path)
    except Exception as exc:  # network/edge service errors
        raise RuntimeError(f"TTS synthesis failed ({exc})") from exc
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("TTS synthesis produced no audio output")
    return out_path


def say(text: str, settings) -> str:
    """Sync wrapper: synthesize ``text`` to a timestamped mp3 under ./data/tts/.

    Returns the mp3 path, or "" if the text is empty. Safe to call both from
    plain threads and from inside a running asyncio event loop (e.g. the
    Telegram handler): in the latter case the coroutine runs on a private
    event loop in a worker thread.
    """
    if not text or not text.strip():
        return ""
    out_dir = os.path.join(".", "data", "tts")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out_path = os.path.join(out_dir, f"tts-{stamp}.mp3")
    voice = getattr(settings, "tts_voice", DEFAULT_VOICE) or DEFAULT_VOICE
    rate = getattr(settings, "tts_rate", DEFAULT_RATE) or DEFAULT_RATE
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No loop running here — safe to use asyncio.run directly.
            asyncio.run(synthesize(text, out_path, voice, rate))
        else:
            # Inside a running event loop: asyncio.run() is illegal here, so
            # run the coroutine on a fresh loop in a worker thread.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run,
                                     synthesize(text, out_path, voice, rate))
                future.result()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"TTS failed ({exc})") from exc
    return out_path
