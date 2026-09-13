"""Tests for the settings API (simon/settings_api.py)."""

from __future__ import annotations

import pytest

from simon import settings_api as sa


ENV_SAMPLE = """\
# Brain
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=
LLM_MODEL=gpt-oss:20b  # smart brain
# Voice
TTS_VOICE=en-GB-RyanNeural
TELEGRAM_BOT_TOKEN=123456:ABCdefGhIJKlmn
"""


def test_read_env_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text(ENV_SAMPLE)
    values, lines = sa.read_env(env)
    assert values["LLM_MODEL"] == "gpt-oss:20b"     # inline comment stripped
    assert values["LLM_API_KEY"] == ""
    assert values["TELEGRAM_BOT_TOKEN"] == "123456:ABCdefGhIJKlmn"
    assert lines[0] == "# Brain"                     # original lines intact


def test_write_env_preserves_comments_and_order(tmp_path):
    env = tmp_path / ".env"
    env.write_text(ENV_SAMPLE)
    changed = sa.write_env({"LLM_MODEL": "qwen3:8b", "NEW_KEY": "x"}, env)
    text = env.read_text()
    assert "LLM_MODEL=qwen3:8b  # smart brain" in text  # comment preserved
    assert text.index("LLM_BASE_URL") < text.index("LLM_MODEL")  # order kept
    assert "NEW_KEY=x" in text                           # new key appended
    assert set(changed) == {"LLM_MODEL", "NEW_KEY"}
    assert (tmp_path / ".env.bak").exists()              # backup written


def test_mask_secret_and_plain():
    assert sa.mask("TELEGRAM_BOT_TOKEN", "123456:ABCdefGhIJKlmn") == "••••••••Klmn"
    assert sa.mask("SHORT_KEY", "abc") == "••••••••"
    assert sa.mask("LLM_MODEL", "gpt-oss:20b") == "gpt-oss:20b"  # not a secret
    assert sa.mask("LLM_API_KEY", "") == ""


def test_whitelist():
    assert "LLM_MODEL" in sa.EDITABLE_KEYS
    assert "SIMON_ALLOW_SHELL" in sa.EDITABLE_KEYS
    assert "PATH" not in sa.EDITABLE_KEYS
