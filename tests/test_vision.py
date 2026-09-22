"""Vision: dedicated multimodal model, no token cap, VRAM-safe."""

import types

from simon.config import Settings
from simon.tools import vision


def _fake_openai(monkeypatch, content="a form with fields"):
    captured = {}

    class Msg:
        pass

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            m = types.SimpleNamespace(content=content)
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=m)])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        chat = types.SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(vision, "OpenAI", FakeOpenAI, raising=False)
    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    return captured


def test_describe_image_uses_dedicated_vision_model(tmp_path, monkeypatch):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    captured = _fake_openai(monkeypatch)
    s = Settings(simon_computer_vision=True,
                 llm_model="gpt-oss:20b", llm_vision_model="qwen3.5:9b")
    out = vision.describe_image(str(img), s)
    assert out == "a form with fields"
    assert captured["model"] == "qwen3.5:9b"
    # qwen3.5-vision returns EMPTY when token-capped — the cap must stay off.
    assert "max_tokens" not in captured
    # occasional-use model must not stay resident (VRAM budget).
    assert captured["extra_body"]["keep_alive"] == "0"


def test_describe_image_falls_back_to_llm_model(tmp_path, monkeypatch):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    captured = _fake_openai(monkeypatch)
    s = Settings(simon_computer_vision=True, llm_model="gpt-oss:20b",
                 llm_vision_model="")
    vision.describe_image(str(img), s)
    assert captured["model"] == "gpt-oss:20b"


def test_describe_image_gate_off(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    assert vision.describe_image(
        str(img), Settings(simon_computer_vision=False)) is None
