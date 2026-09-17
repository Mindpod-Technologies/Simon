"""Per-person profiles: names, attribution, and persona injection.

Guards the contract: family members are addressed by name (never 'sir'),
their facts are attributed to them, and auto-capture never overwrites a
name that was chosen deliberately.
"""

import pytest

from simon import memory, profiles
from simon.agent import Agent
from simon.config import Settings


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH", str(tmp_path / "simon.db"))
    return str(tmp_path / "simon.db")


def test_set_and_get_profile(db):
    profiles.set_profile("8959938993", "Alicia")
    prof = profiles.get_profile("8959938993")
    assert prof["name"] == "Alicia"
    assert profiles.display_name("8959938993") == "Alicia"
    assert profiles.display_name("owner") == ""  # owner has no profile


def test_ensure_name_never_overwrites(db):
    assert profiles.ensure_name("s1", "Ali") is True       # first capture
    assert profiles.ensure_name("s1", "Alicia") is False   # keeps the first
    assert profiles.display_name("s1") == "Ali"
    profiles.set_profile("s1", "Alicia")                   # deliberate rename
    assert profiles.ensure_name("s1", "Ali") is False      # auto never wins
    assert profiles.display_name("s1") == "Alicia"


def test_form_of_address_preserved_on_rename(db):
    profiles.set_profile("s2", "Alicia", form="ma'am")
    profiles.set_profile("s2", "Ali")          # rename without form
    assert profiles.get_profile("s2")["form"] == "ma'am"


class _CaptureLLM:
    model = "smart"
    model_fast = "fast"
    frontier_enabled = False

    def __init__(self):
        self.prompts = []

    def chat(self, messages, tools=None, model=None):
        self.prompts.append(messages[0]["content"])
        return {"content": "Good evening, Alicia.", "tool_calls": []}


class _Registry:
    def schemas(self):
        return []

    def call(self, name, args):  # pragma: no cover
        return ""


def test_persona_prompt_names_family_member(db):
    profiles.set_profile("wife-session", "Alicia")
    llm = _CaptureLLM()
    agent = Agent(Settings(), registry=_Registry(), session_id="wife-session",
                  llm=llm)
    agent.handle("evening Simon")
    prompt = llm.prompts[0]
    assert "Alicia" in prompt
    assert "not the owner" in prompt
    assert "sir' is reserved" in prompt


def test_owner_session_prompt_unchanged(db):
    llm = _CaptureLLM()
    agent = Agent(Settings(), registry=_Registry(), session_id="owner",
                  llm=llm)
    agent.handle("evening Simon")
    prompt = llm.prompts[0]
    assert "You are speaking with" not in prompt
