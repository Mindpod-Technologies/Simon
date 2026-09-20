"""Tests for the computer-use and dedicated-browser tools (no network/GUI)."""
from __future__ import annotations

import sys
import types

import pytest

from simon.config import Settings
from simon.tools import ToolRegistry
from simon.tools import browser as browser_mod
from simon.tools import computer as computer_mod
from simon.tools import vision as vision_mod


@pytest.fixture
def settings(tmp_path):
    return Settings(
        simon_workspace_dir=str(tmp_path / "workspace"),
        simon_computer_use=False,
        simon_browser_enabled=True,
    )


def _darwin(monkeypatch):
    monkeypatch.setattr(computer_mod.platform, "system", lambda: "Darwin")


# --- registration gating ----------------------------------------------------

def test_computer_tools_absent_when_flag_off(settings, monkeypatch):
    _darwin(monkeypatch)
    registry = ToolRegistry()
    computer_mod.register_computer_tools(registry, settings)
    assert "computer_screenshot" not in registry
    assert len(registry) == 0


def test_computer_tools_absent_on_non_darwin(settings, monkeypatch):
    monkeypatch.setattr(computer_mod.platform, "system", lambda: "Linux")
    registry = ToolRegistry()
    computer_mod.register_computer_tools(
        registry, Settings(simon_computer_use=True),
    )
    assert "computer_screenshot" not in registry
    assert len(registry) == 0


def test_computer_tools_registered_on_darwin(settings, monkeypatch):
    _darwin(monkeypatch)
    registry = ToolRegistry()
    computer_mod.register_computer_tools(
        registry, Settings(simon_computer_use=True),
    )
    for name in (
        "computer_screenshot", "computer_click", "computer_type",
        "computer_key", "computer_open_app", "computer_applescript",
        "computer_active_app", "computer_list_windows",
    ):
        assert name in registry


def test_browser_tools_registered_when_enabled(settings):
    registry = ToolRegistry()
    browser_mod.register_browser_tools(registry, settings)
    for name in (
        "browser_goto", "browser_click", "browser_type", "browser_screenshot",
        "browser_eval", "browser_scroll", "browser_tabs", "browser_close_tab",
    ):
        assert name in registry


def test_browser_tools_absent_when_disabled(settings):
    registry = ToolRegistry()
    browser_mod.register_browser_tools(
        registry, Settings(simon_browser_enabled=False),
    )
    assert len(registry) == 0


# --- computer tool behaviour ------------------------------------------------

def test_computer_click_guidance_without_cliclick(settings, monkeypatch):
    _darwin(monkeypatch)
    monkeypatch.setattr(computer_mod.shutil, "which", lambda _: None)
    registry = ToolRegistry()
    computer_mod.register_computer_tools(
        registry, Settings(simon_computer_use=True),
    )
    result = registry.call("computer_click", {"x": 10, "y": 20})
    assert result.startswith("Error:")
    assert "cliclick" in result


def test_computer_applescript_argv(settings, monkeypatch):
    _darwin(monkeypatch)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        proc = types.SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        return proc

    monkeypatch.setattr(computer_mod.subprocess, "run", fake_run)
    registry = ToolRegistry()
    computer_mod.register_computer_tools(
        registry, Settings(simon_computer_use=True),
    )
    result = registry.call("computer_applescript", {"script": "beep"})
    assert result == "ok"
    assert calls == [["osascript", "-e", "beep"]]


def test_computer_key_unknown_key(settings, monkeypatch):
    _darwin(monkeypatch)
    registry = ToolRegistry()
    computer_mod.register_computer_tools(
        registry, Settings(simon_computer_use=True),
    )
    result = registry.call("computer_key", {"key": "not-a-key"})
    assert result.startswith("Error:")


# --- browser with stubbed playwright ----------------------------------------

class _FakeLocator:
    def __init__(self, page):
        self._page = page

    @property
    def first(self):
        return self

    def inner_text(self):
        return self._page._text

    def click(self, timeout=None):
        return None

    def fill(self, text, timeout=None):
        self._page._filled = text

    def input_value(self):
        return getattr(self._page, "_filled", "")


class _FakePage:
    def __init__(self):
        self._text = "Hello from the fake page"
        self._title = "Fake Title"
        self._fields = []

    def set_default_timeout(self, ms):
        return None

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url

    def title(self):
        return self._title

    def locator(self, selector):
        return _FakeLocator(self)

    def get_by_text(self, text, exact=False):
        return _FakeLocator(self)

    def evaluate(self, js):
        if "querySelectorAll" in js:
            return self._fields
        return {"js": js}

    def screenshot(self, path=None, full_page=False):
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")

    def close(self):
        return None

    @property
    def mouse(self):
        return types.SimpleNamespace(wheel=lambda dx, dy: None)


class _FakeContext:
    def __init__(self):
        self.pages = [_FakePage()]

    def new_page(self):
        page = _FakePage()
        self.pages.append(page)
        return page

    def close(self):
        return None


@pytest.fixture
def fake_playwright(monkeypatch):
    """Stub the playwright package with a fake persistent context."""
    context = _FakeContext()
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: types.SimpleNamespace(
        start=lambda: types.SimpleNamespace(
            chromium=types.SimpleNamespace(
                launch_persistent_context=lambda *a, **k: context,
            ),
        ),
        stop=lambda: None,
    )
    pkg = types.ModuleType("playwright")
    pkg.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(browser_mod, "_pw", None)
    monkeypatch.setattr(browser_mod, "_context", None)
    return context


def test_browser_goto_returns_title_and_text(settings, fake_playwright):
    registry = ToolRegistry()
    browser_mod.register_browser_tools(registry, settings)
    result = registry.call("browser_goto", {"url": "https://example.com"})
    assert "Title: Fake Title" in result
    assert "Hello from the fake page" in result


def test_browser_eval_returns_json(settings, fake_playwright):
    registry = ToolRegistry()
    browser_mod.register_browser_tools(registry, settings)
    result = registry.call("browser_eval", {"js": "1+1"})
    assert '"js": "1+1"' in result


def test_browser_missing_playwright_errors_gracefully(settings, monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)  # import fails
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    monkeypatch.setattr(browser_mod, "_pw", None)
    monkeypatch.setattr(browser_mod, "_context", None)
    registry = ToolRegistry()
    browser_mod.register_browser_tools(registry, settings)
    result = registry.call("browser_goto", {"url": "https://example.com"})
    assert result.startswith("Error:")


# --- vision helper -----------------------------------------------------------

def test_vision_disabled_returns_none(settings):
    assert vision_mod.describe_image("/nonexistent.png", settings) is None


def test_vision_without_openai_returns_none(monkeypatch):
    settings = Settings(simon_computer_vision=True)
    monkeypatch.setitem(sys.modules, "openai", None)  # import fails
    assert vision_mod.describe_image("/nonexistent.png", settings) is None


def test_browser_goto_includes_form_map(settings, fake_playwright):
    """The form map is what lets the model fill fields with real selectors."""
    fake_playwright.pages[0]._fields = [
        {"tag": "input", "type": "text", "name": "custname", "id": "",
         "text": "", "placeholder": "Customer name"},
        {"tag": "button", "type": "submit", "name": "", "id": "",
         "text": "Submit order", "placeholder": ""},
    ]
    registry = ToolRegistry()
    browser_mod.register_browser_tools(registry, settings)
    result = registry.call("browser_goto", {"url": "https://x.test/form"})
    assert "Form fields" in result
    assert "name=custname" in result
    assert "Submit order" in result


def test_browser_type_reads_back_the_value(settings, fake_playwright):
    registry = ToolRegistry()
    browser_mod.register_browser_tools(registry, settings)
    out = registry.call("browser_type",
                        {"selector": "input[name=custname]", "text": "Simon QA"})
    assert "verified" in out and "Simon QA" in out


def test_browser_type_reports_failed_fill(settings, fake_playwright):
    """If the fill doesn't stick, the tool must say so — never fake success."""
    fake_playwright.pages[0]._filled = ""  # fill() sets it; force mismatch:
    registry = ToolRegistry()
    browser_mod.register_browser_tools(registry, settings)

    class RefusingLocator(_FakeLocator):
        def fill(self, text, timeout=None):
            raise RuntimeError("element is not editable")
    fake_playwright.pages[0].locator = lambda s: RefusingLocator(fake_playwright.pages[0])
    fake_playwright.pages[0].get_by_label = lambda s, exact=False: RefusingLocator(fake_playwright.pages[0])
    out = registry.call("browser_type",
                        {"selector": "#x", "text": "hello"})
    assert "Error" in out and "no fillable field matched" in out


def test_browser_type_forgiving_selector(settings, fake_playwright):
    """A bare field name ('custname') must resolve to [name=custname]."""
    registry = ToolRegistry()
    browser_mod.register_browser_tools(registry, settings)
    out = registry.call("browser_type",
                        {"selector": "custname", "text": "Simon QA"})
    assert "verified" in out and "Simon QA" in out
