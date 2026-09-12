"""No-network tests for interface config fields and module imports.

The Slack / Teams (and possibly Telegram) dependencies are optional, so we
inject light fakes into ``sys.modules`` to verify that the interface
modules import and expose their entry points without the heavy packages
installed.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from simon.config import Settings


def _fake_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


@pytest.fixture
def stubbed_optional_deps(monkeypatch):
    """Inject fakes for slack_bolt, botbuilder.* and telegram (if absent)."""
    fakes: dict[str, types.ModuleType] = {}

    # --- slack_bolt -------------------------------------------------------
    slack_bolt = _fake_module("slack_bolt")
    slack_async_app = _fake_module("slack_bolt.async_app",
                                   AsyncApp=MagicMock())
    slack_socket = _fake_module("slack_bolt.adapter.socket_mode",
                                SocketModeHandler=MagicMock())
    slack_socket_aiohttp = _fake_module(
        "slack_bolt.adapter.socket_mode.aiohttp",
        AsyncSocketModeHandler=MagicMock())
    fakes.update({
        "slack_bolt": slack_bolt,
        "slack_bolt.async_app": slack_async_app,
        "slack_bolt.adapter": _fake_module("slack_bolt.adapter"),
        "slack_bolt.adapter.socket_mode": slack_socket,
        "slack_bolt.adapter.socket_mode.aiohttp": slack_socket_aiohttp,
    })

    # --- botbuilder -------------------------------------------------------
    schema = _fake_module("botbuilder.schema")
    for cls in ("Activity", "ActivityTypes", "Attachment"):
        setattr(schema, cls, MagicMock())
    core = _fake_module("botbuilder.core")
    for cls in ("ActivityHandler", "BotFrameworkAdapter",
                "BotFrameworkAdapterSettings", "TurnContext"):
        setattr(core, cls, MagicMock())
    fakes.update({
        "botbuilder": _fake_module("botbuilder"),
        "botbuilder.core": core,
        "botbuilder.schema": schema,
    })

    # --- aiohttp (only if the real package is missing) --------------------
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        fakes["aiohttp"] = _fake_module("aiohttp", web=MagicMock())

    # --- voice heavy deps (only if missing) -------------------------------
    for name in ("edge_tts", "faster_whisper", "sounddevice", "soundfile"):
        try:
            __import__(name)
        except ImportError:
            fakes[name] = _fake_module(name)

    # --- telegram (only if the real package is missing) -------------------
    try:
        import telegram  # noqa: F401
    except ImportError:
        telegram = _fake_module("telegram", Update=MagicMock(),
                                Bot=MagicMock())
        telegram_ext = _fake_module(
            "telegram.ext",
            Application=MagicMock(),
            CommandHandler=MagicMock(),
            ContextTypes=MagicMock(),
            MessageHandler=MagicMock(),
            filters=MagicMock(),
        )
        fakes.update({
            "telegram": telegram,
            "telegram.ext": telegram_ext,
        })

    for name, mod in fakes.items():
        monkeypatch.setitem(sys.modules, name, mod)
    yield fakes


class TestSettingsFields:
    def test_slack_fields_exist_with_defaults(self):
        s = Settings()
        assert s.slack_bot_token == ""
        assert s.slack_app_token == ""
        assert s.slack_allowed_user_ids == ""

    def test_teams_fields_exist_with_defaults(self):
        s = Settings()
        assert s.teams_app_id == ""
        assert s.teams_app_password == ""
        assert s.teams_allowed_user_ids == ""
        assert s.teams_port == 3978

    def test_public_base_url_default(self):
        s = Settings()
        assert s.simon_public_base_url == ""

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("TEAMS_PORT", "4000")
        s = Settings()
        assert s.slack_bot_token == "xoxb-test"
        assert s.teams_port == 4000


class TestRunPyArgparse:
    @pytest.mark.parametrize("mode", ["slack", "teams", "all"])
    def test_mode_accepted(self, mode):
        import run

        with pytest.MonkeyPatch.context() as mp:
            called = {}
            mp.setattr(sys, "argv", ["run.py", mode])
            mp.setattr(run, f"cmd_{mode}",
                       lambda: called.setdefault("mode", mode))
            run.main()
        assert called["mode"] == mode

    def test_invalid_mode_rejected(self):
        import run

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sys, "argv", ["run.py", "carrier-pigeon"])
            with pytest.raises(SystemExit):
                run.main()


class TestInterfaceModules:
    def test_slack_module_entry_points(self, stubbed_optional_deps):
        import simon.interfaces.slack_bot as slack_bot

        assert callable(slack_bot.run_slack)
        assert callable(slack_bot.run_slack_async)

    def test_teams_module_entry_points(self, stubbed_optional_deps):
        import simon.interfaces.teams_bot as teams_bot

        assert callable(teams_bot.run_teams)
        assert callable(teams_bot.run_teams_async)
        assert callable(teams_bot.create_teams_app)

    def test_telegram_module_entry_points(self, stubbed_optional_deps):
        import simon.interfaces.telegram_bot as telegram_bot

        assert callable(telegram_bot.run_telegram)
        assert callable(telegram_bot.run_telegram_async)
