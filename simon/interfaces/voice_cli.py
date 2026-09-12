"""Voice CLI interface for Simon.

Loop: press Enter to start recording, Enter again to stop → transcribe →
Agent.handle → print reply → synthesise speech → play it.
Ctrl+C exits gracefully.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from simon.agent import Agent
from simon.voice import stt, tts

SAMPLE_RATE = 16000
CHANNELS = 1


def record_audio() -> str:
    """Enter to start recording, Enter to stop. Returns wav path."""
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    print("Press Enter to speak (Ctrl+C to quit)...", flush=True)
    input()  # start recording
    print("Recording — press Enter to stop.", flush=True)

    chunks: list = []
    recording = {"on": True}

    def callback(indata, frames, time_info, status):  # noqa: ARG001
        if recording["on"]:
            chunks.append(indata.copy())

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=CHANNELS, callback=callback
    )
    stream.start()
    try:
        input()  # stop recording
    finally:
        recording["on"] = False
        stream.stop()
        stream.close()
    if not chunks:
        raise RuntimeError("No audio captured.")
    audio = np.concatenate(chunks, axis=0)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    sf.write(tmp.name, audio, SAMPLE_RATE)
    return tmp.name


def play_audio(path: str) -> None:
    """Play an audio file with whatever player is available."""
    players = (
        ["afplay", path],  # macOS
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
        ["aplay", path],  # Linux ALSA
    )
    for cmd in players:
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, check=False)
                return
            except Exception:  # noqa: BLE001
                continue
    print("(No audio player found — install afplay/ffplay/aplay.)",
          file=sys.stderr)


def run_voice_cli(settings) -> None:
    """Run the interactive voice loop (blocking)."""
    agent = Agent(settings, session_id="voice-cli")
    print("Simon voice interface ready, sir.")
    try:
        while True:
            try:
                wav_path = record_audio()
            except KeyboardInterrupt:
                # Ctrl+C while waiting/recording: exit cleanly.
                print()
                break
            try:
                text = stt.transcribe(wav_path, settings)
            finally:
                Path(wav_path).unlink(missing_ok=True)
            if not text.strip():
                print("Simon: I'm sorry sir, I didn't catch that.")
                continue
            print(f"You: {text}")
            try:
                reply = agent.handle(text)
            except Exception:  # noqa: BLE001
                reply = (
                    "I do apologise, sir — something went wrong on my end."
                )
            print(f"Simon: {reply}")
            try:
                mp3_path = tts.say(reply, settings)
                play_audio(mp3_path)
            except Exception:  # noqa: BLE001
                print("(TTS/playback unavailable.)", file=sys.stderr)
    except KeyboardInterrupt:
        print()
    print("Very good, sir. Until next time.")
