"""Voice pipeline: British TTS (edge-tts) + STT (faster-whisper / OpenAI Whisper API)."""

from .tts import say, synthesize
from .stt import transcribe

__all__ = ["say", "synthesize", "transcribe"]
