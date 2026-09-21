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


def test_disabled_without_key(monkeypatch):
    monkeypatch.setenv("SIMON_DECISION_BACKEND", "api")
    assert not decisions.enabled()
    assert decisions.route_turn("hello") is None
    assert decisions.judge("q", "a", "rubric") is None


def test_auto_backend_uses_laya_when_no_key(monkeypatch):
    monkeypatch.delenv("SIMON_DECISION_BACKEND", raising=False)
    monkeypatch.delenv("SIMON_JEV_API_KEY", raising=False)
    monkeypatch.setattr(decisions, "_LAYA_AGENT",
                        _FakeLayaAgent({"fast": 0.9, "smart": 0.1}))
    assert decisions._backend() == "laya"
    assert decisions.route_turn("good evening") == ("fast", 0.9)


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
    assert "decision fast" in llm.last_route_reason


def test_router_uses_confident_jev_smart(monkeypatch):
    _enable(monkeypatch, '{"choice": "smart", "confidence": 0.88}')
    llm = _JevLLM()
    assert llm.route_model("research this for me", 3, 0) == "gpt-oss:20b"
    assert "decision smart" in llm.last_route_reason


def test_router_falls_back_below_confidence(monkeypatch):
    _enable(monkeypatch, '{"choice": "fast", "confidence": 0.2}')
    llm = _JevLLM()
    # keyword classifier decides instead (greeting → fast anyway)
    model = llm.route_model("good evening sir", 3, 0)
    assert "decision" not in llm.last_route_reason


def test_router_falls_back_when_jev_unreachable(monkeypatch):
    _enable(monkeypatch, "")  # will fail to parse → None
    llm = _JevLLM()
    llm.route_model("what is 2+2?", 1, 0)
    assert "decision" not in llm.last_route_reason


# --- Laya backend ------------------------------------------------------------

class _FakeLayaAgent:
    def __init__(self, probs):
        self._probs = probs

    def predict(self, state, questions):
        qid = next(iter(questions))
        probs = dict(self._probs)
        return {"answers": {qid: {"probabilities": probs}}}


def _force_laya(monkeypatch, probs):
    monkeypatch.setenv("SIMON_DECISION_BACKEND", "laya")
    monkeypatch.setattr(decisions, "_LAYA_AGENT", _FakeLayaAgent(probs))


def test_laya_backend_routes_by_probability(monkeypatch):
    _force_laya(monkeypatch, {"fast": 0.9, "smart": 0.1})
    assert decisions.route_turn("good evening") == ("fast", 0.9)


def test_laya_backend_picks_smart(monkeypatch):
    _force_laya(monkeypatch, {"fast": 0.2, "smart": 0.8})
    assert decisions.route_turn("write a report") == ("smart", 0.8)


def test_laya_backend_soft_fails(monkeypatch):
    class Boom:
        def predict(self, state, questions):
            raise RuntimeError("model blew up")
    monkeypatch.setenv("SIMON_DECISION_BACKEND", "laya")
    monkeypatch.setattr(decisions, "_LAYA_AGENT", Boom())
    assert decisions.route_turn("anything") is None


def test_laya_judge_noul(monkeypatch):
    _force_laya(monkeypatch, {"yes": 0.93, "no": 0.07})
    verdict = decisions.judge("who are you?", "I am Simon.", "must say Simon")
    assert verdict["pass"] is True and verdict["score"] == 0.93


def test_laya_judge_fail(monkeypatch):
    _force_laya(monkeypatch, {"yes": 0.1, "no": 0.9})
    verdict = decisions.judge("who are you?", "I am ChatGPT.",
                              "must say Simon")
    assert verdict["pass"] is False


def test_auto_backend_prefers_api_key(monkeypatch):
    monkeypatch.setenv("SIMON_JEV_API_KEY", "k")
    monkeypatch.delenv("SIMON_DECISION_BACKEND", raising=False)
    assert decisions._backend() == "api"
