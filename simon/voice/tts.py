"""Text-to-speech via edge-tts (British JARVIS voice by default)."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

import edge_tts

DEFAULT_VOICE = "en-GB-RyanNeural"
DEFAULT_RATE = "+0%"


async def synthesize(text: str, out_path: str, voice: str = DEFAULT_VOICE, rate: str = DEFAULT_RATE) -> str:
    """Synthesize ``text`` to an mp3 at ``out_path`` using edge-tts.

    Returns ``out_path``. Raises ValueError on empty text, RuntimeError on
    synthesis failure.
    """
    if not text or not text.strip():
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
