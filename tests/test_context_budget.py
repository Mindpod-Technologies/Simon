"""Context budgeting: long sessions must never overflow the model context."""

from simon.agent import Agent
from simon.config import Settings
from simon import memory


class _LLM:
    model = "smart"
    model_fast = "fast"
    frontier_enabled = False

    def chat(self, messages, tools=None, model=None):
        return {"content": "ok", "tool_calls": []}


class _Registry:
    def schemas(self):
        return []

    def call(self, name, args):  # pragma: no cover
        return ""


def test_history_trimmed_to_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    memory.init_db()
    # 60 large turns (~400 chars each ≈ 100 tokens) → ~6000 tokens total.
    for i in range(60):
        memory.add_message("long-session", "user", f"q{i} " + "x" * 380)
        memory.add_message("long-session", "assistant", f"a{i} " + "y" * 380)
    agent = Agent(Settings(llm_history_budget_tokens=2000),
                  registry=_Registry(), session_id="long-session",
                  llm=_LLM())
    msgs = agent._build_messages("fresh question")
    history = [m for m in msgs if m["role"] in ("user", "assistant")]
    est = sum(len(m["content"]) for m in history) // 4
    assert est <= 2000
    # The newest turns survive; the oldest are gone.
    assert any("q59" in m["content"] for m in history)
    assert not any("q0 " in m["content"] for m in history)
    # The current message is always present exactly once.
    assert history[-1]["content"] == "fresh question"
    assert sum(1 for m in history if m["content"] == "fresh question") == 1


def test_short_history_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    memory.init_db()
    memory.add_message("s", "user", "hello")
    memory.add_message("s", "assistant", "hi")
    agent = Agent(Settings(), registry=_Registry(), session_id="s",
                  llm=_LLM())
    msgs = agent._build_messages("how are you")
    contents = [m["content"] for m in msgs if m["role"] in ("user", "assistant")]
    assert contents == ["hello", "hi", "how are you"]


def test_num_ctx_per_tier(monkeypatch):
    """llm.chat must send num_ctx sized for the chosen model tier."""
    from simon.llm import LLM
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            class Msg:
                content = "ok"
                tool_calls = None
            class Choice:
                message = Msg()
            class Resp:
                choices = [Choice()]
                usage = None
            return Resp()

    llm = LLM(Settings(llm_base_url="http://localhost:11434/v1",
                       llm_model="gpt-oss:20b", llm_model_fast="qwen3:8b"))
    monkeypatch.setattr(llm._client.chat, "completions", FakeCompletions())
    llm.chat([{"role": "user", "content": "hi"}], model="qwen3:8b")
    assert captured["extra_body"]["num_ctx"] == 8192
    llm.chat([{"role": "user", "content": "hi"}], model="gpt-oss:20b")
    assert captured["extra_body"]["num_ctx"] == 16384
