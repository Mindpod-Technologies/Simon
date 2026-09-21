"""Decision layer: Jev routing + eval judging, always soft-failing."""

import pytest

from simon import decisions
from simon.llm import LLM
from simon.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("SIMON_JEV_API_KEY", "SIMON_JEV_BASE_URL", "SIMON_JEV_MODEL"):
        monkeypatch.delenv(var, raising=False)


def _enable(monkeypatch, payload):
    monkeypatch.setenv("SIMON_JEV_API_KEY", "test-key")

    class FakeMessage:
        content = payload

    class FakeChoice:
        message = FakeMessage()

    class FakeResp:
        choices = [FakeChoice()]

    class FakeClient:
        def __init__(self, **kw):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**_kw):
                    return FakeResp()

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeClient)


def test_disabled_without_key():
    assert not decisions.enabled()
    assert decisions.route_turn("hello") is None
    assert decisions.judge("q", "a", "rubric") is None


def test_route_turn_parses_typed_decision(monkeypatch):
    _enable(monkeypatch, '{"choice": "fast", "confidence": 0.91}')
    assert decisions.route_turn("good evening") == ("fast", 0.91)


def test_route_turn_tolerates_fences_and_prose(monkeypatch):
    _enable(monkeypatch, 'Sure!\n```json\n{"choice": "smart", "confidence": 0.7}\n```')
    assert decisions.route_turn("write a report") == ("smart", 0.7)


def test_route_turn_soft_fails_on_garbage(monkeypatch):
    _enable(monkeypatch, "I cannot decide that, sir.")
    assert decisions.route_turn("anything") is None


def test_route_turn_soft_fails_on_bad_choice(monkeypatch):
    _enable(monkeypatch, '{"choice": "maybe", "confidence": 0.9}')
    assert decisions.route_turn("anything") is None


def test_judge_parses_verdict(monkeypatch):
    _enable(monkeypatch,
            '{"pass": true, "score": 0.95, "confidence": 0.99, "reason": "on rubric"}')
    verdict = decisions.judge("who are you?", "I am Simon.", "must say Simon")
    assert verdict["pass"] is True
    assert verdict["confidence"] == 0.99


def test_judge_soft_fails_on_missing_pass(monkeypatch):
    _enable(monkeypatch, '{"score": 0.5}')
    assert decisions.judge("q", "a", "r") is None


# --- routing integration -----------------------------------------------------

class _JevLLM(LLM):
    """Real LLM class, frontier off; router on."""

    def __init__(self):
        settings = Settings(llm_model="gpt-oss:20b",
                            llm_model_fast="qwen3:8b",
                            llm_router_enabled=True)
        LLM.__init__(self, settings)


def test_router_uses_confident_jev_fast(monkeypatch):
    _enable(monkeypatch, '{"choice": "fast", "confidence": 0.95}')
    llm = _JevLLM()
    assert llm.route_model("hello there", 3, 0) == "qwen3:8b"
    assert "jev fast" in llm.last_route_reason


def test_router_uses_confident_jev_smart(monkeypatch):
    _enable(monkeypatch, '{"choice": "smart", "confidence": 0.88}')
    llm = _JevLLM()
    assert llm.route_model("research this for me", 3, 0) == "gpt-oss:20b"
    assert "jev smart" in llm.last_route_reason


def test_router_falls_back_below_confidence(monkeypatch):
    _enable(monkeypatch, '{"choice": "fast", "confidence": 0.2}')
    llm = _JevLLM()
    # keyword classifier decides instead (greeting → fast anyway)
    model = llm.route_model("good evening sir", 3, 0)
    assert "jev" not in llm.last_route_reason


def test_router_falls_back_when_jev_unreachable(monkeypatch):
    _enable(monkeypatch, "")  # will fail to parse → None
    llm = _JevLLM()
    llm.route_model("what is 2+2?", 1, 0)
    assert "jev" not in llm.last_route_reason
