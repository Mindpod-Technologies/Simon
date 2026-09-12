"""Speech-to-text: faster-whisper locally, or OpenAI Whisper API if stt_model == 'openai'."""

from __future__ import annotations

import os

_model = None  # cached faster-whisper WhisperModel


def _transcribe_openai(audio_path: str, settings) -> str:
    """Transcribe via the OpenAI Whisper API (openai SDK, honors settings.llm_base_url)."""
    if not os.path.exists(audio_path):
        raise RuntimeError(f"audio file not found: {audio_path}")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "stt_model='openai' requires the openai package (pip install openai)"
        ) from exc
    api_key = getattr(settings, "llm_api_key", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("stt_model='openai' needs an API key (llm_api_key / OPENAI_API_KEY)")
    client = OpenAI(api_key=api_key, base_url=getattr(settings, "llm_base_url", None) or None)
    try:
        with open(audio_path, "rb") as fh:
            result = client.audio.transcriptions.create(model="whisper-1", file=fh)
    except Exception as exc:
        raise RuntimeError(f"OpenAI transcription failed ({exc})") from exc
    return (getattr(result, "text", "") or "").strip()


def _transcribe_local(audio_path: str, settings) -> str:
    """Transcribe locally with faster-whisper (model cached at module level)."""
    global _model
    if not os.path.exists(audio_path):
        raise RuntimeError(f"audio file not found: {audio_path}")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "local STT requires faster-whisper (pip install faster-whisper); "
            "or set stt_model='openai' to use the Whisper API"
        ) from exc
    if _model is None:
        _model = WhisperModel(settings.stt_model, device="cpu", compute_type="int8")
    try:
        segments, _info = _model.transcribe(audio_path)
        text = " ".join(seg.text.strip() for seg in segments if seg.text).strip()
    except Exception as exc:
        raise RuntimeError(f"local transcription failed ({exc})") from exc
    return text


def transcribe(audio_path: str, settings) -> str:
    """Transcribe ``audio_path`` to text.

    Uses the OpenAI Whisper API when ``settings.stt_model == 'openai'``,
    otherwise a local faster-whisper model named by ``settings.stt_model``.
    """
    if not audio_path:
        raise RuntimeError("no audio path provided for transcription")
    if getattr(settings, "stt_model", "") == "openai":
        return _transcribe_openai(audio_path, settings)
    return _transcribe_local(audio_path, settings)
