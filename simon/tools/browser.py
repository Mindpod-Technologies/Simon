"""Dedicated browser tools for Simon, driven by Playwright (opt-out).

Simon gets his own Chromium browser with a persistent profile at
./data/browser-profile, so logins survive restarts. Playwright is imported
lazily inside each tool so this module imports fine without it installed.

Registered by build_default_registry() when SIMON_BROWSER_ENABLED=true
(default true). SIMON_BROWSER_HEADLESS (default true) controls headless mode;
set false to watch Simon work in a visible window.

The sync Playwright API must not run inside an asyncio event loop; the agent
loop is synchronous, and a module-level lock guards against concurrent calls.
"""
from __future__ import annotations

import atexit
import json
import logging
import threading
import time
from pathlib import Path

from .base import Tool
from .vision import describe_image

log = logging.getLogger(__name__)

_PAGE_TIMEOUT_MS = 60_000
_TEXT_CAP = 4000

_lock = threading.RLock()  # reentrant: tool wrappers call _get_context under lock
_pw = None          # sync_playwright() manager
_context = None     # persistent BrowserContext


def _profile_dir() -> Path:
    path = Path("./data/browser-profile")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _shutdown() -> None:
    global _pw, _context
    with _lock:
        try:
            if _context is not None:
                _context.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if _pw is not None:
                _pw.stop()
        except Exception:  # noqa: BLE001
            pass
        _pw = None
        _context = None


def _get_context(settings):
    """Lazily launch (or attach) the browser context (thread-safe).

    Two modes:
    - attach (SIMON_BROWSER_CDP_URL set, e.g. http://localhost:9222): join
      the USER's own Chrome — their tabs, their logins. Takeover mode.
    - launch (default): Simon's own persistent Chromium profile.
    """
    global _pw, _context
    with _lock:
        if _context is not None:
            return _context
        from playwright.sync_api import sync_playwright  # lazy import

        cdp_url = (getattr(settings, "simon_browser_cdp_url", "") or "")
        _pw = sync_playwright().start()
        if cdp_url:
            # Only loopback attach is permitted — never a remote browser.
            if not cdp_url.startswith(("http://localhost", "http://127.0.0.1")):
                raise RuntimeError(
                    "SIMON_BROWSER_CDP_URL must be a localhost URL")
            browser = _pw.chromium.connect_over_cdp(cdp_url)
            contexts = browser.contexts
            _context = contexts[0] if contexts else browser.new_context()
            log.info("browser: attached to user Chrome via %s", cdp_url)
        else:
            _context = _pw.chromium.launch_persistent_context(
                str(_profile_dir()),
                headless=getattr(settings, "simon_browser_headless", True),
            )
        atexit.register(_shutdown)
        return _context


def _reset_context() -> None:
    """Drop the current context after a fatal browser error."""
    global _pw, _context
    _pw = None
    _context = None


def _page(context):
    """Return the active page, creating one if needed."""
    pages = context.pages
    if pages:
        return pages[-1]
    return context.new_page()


def register_browser_tools(registry, settings) -> None:
    """Register Playwright browser tools on the given registry."""
    if not getattr(settings, "simon_browser_enabled", True):
        log.info("browser tools skipped: SIMON_BROWSER_ENABLED is false")
        return

    def _with_page(fn) -> str:
        """Run fn(page) against the shared browser; errors as strings."""
        try:
            with _lock:
                context = _get_context(settings)
                page = _page(context)
                page.set_default_timeout(_PAGE_TIMEOUT_MS)
                return fn(page)
        except Exception as exc:  # noqa: BLE001 - tools must never raise
            _reset_context()
            return f"Error: browser action failed: {exc}"

    def browser_goto(url: str) -> str:
        """Navigate to a URL; return page title + visible text + form map."""
        def go(page):
            # Slow sites (httpbin at night!) need room; domcontentloaded is
            # enough — we only need the DOM, not every tracking pixel.
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            title = page.title()
            try:
                text = page.locator("body").inner_text()
            except Exception:  # noqa: BLE001 - some pages have no body yet
                text = ""
            # Form map: the model needs real selectors to fill anything.
            fields = page.evaluate(
                """() => [...document.querySelectorAll(
                    'input, textarea, select, button, [role=button]')]
                   .filter(e => e.offsetParent !== null)
                   .slice(0, 30)
                   .map(e => ({tag: e.tagName.toLowerCase(),
                               type: e.type || '',
                               name: e.name || '',
                               id: e.id || '',
                               text: (e.innerText || e.value || '').trim()
                                     .slice(0, 40),
                               placeholder: e.placeholder || ''}))""")
            field_lines = []
            for f in fields or []:
                bits = [f["tag"]]
                if f.get("type"): bits.append(f"type={f['type']}")
                if f.get("name"): bits.append(f"name={f['name']}")
                if f.get("id"): bits.append(f"id={f['id']}")
                if f.get("text"): bits.append(f"text='{f['text']}'")
                if f.get("placeholder"): bits.append(f"placeholder='{f['placeholder']}'")
                field_lines.append("  " + " ".join(bits))
            out = f"Title: {title}\n\n{text[:_TEXT_CAP]}"
            if field_lines:
                out += ("\n\nForm fields (use these selectors with "
                        "browser_type/browser_click):\n" + "\n".join(field_lines))
            return out
        return _with_page(go)

    def browser_click(selector_or_text: str) -> str:
        """Click an element: tries visible text first, then a CSS selector."""
        def do(page):
            locator = page.get_by_text(selector_or_text, exact=False).first
            try:
                locator.click(timeout=5_000)
                return f"Clicked text: {selector_or_text}"
            except Exception:  # noqa: BLE001 - fall through to CSS selector
                pass
            page.locator(selector_or_text).first.click()
            return f"Clicked selector: {selector_or_text}"
        return _with_page(do)

    def browser_type(selector: str, text: str) -> str:
        """Fill a form field, then READ IT BACK — filling must never be
        claimed without verification. Selector is forgiving: exact CSS first,
        then [name=...], #id, label text, and placeholder text."""
        def do(page):
            candidates = [selector]
            if not selector.startswith(("#", "[", ".", "input", "textarea",
                                        "select")):
                candidates += [f"[name=\"{selector}\"]", f"#{selector}"]
            candidates += [f"[name=\"{selector.strip('#')}\"]"]
            tried = []
            for cand in dict.fromkeys(candidates):
                loc = None
                try:
                    if cand == selector and " " in selector:
                        loc = page.get_by_label(selector, exact=False).first
                    else:
                        loc = page.locator(cand).first
                    loc.fill(text, timeout=3_000)
                except Exception:  # noqa: BLE001 - try the next candidate
                    tried.append(cand)
                    continue
                actual = loc.input_value()
                if actual == text:
                    return (f"Filled {cand}; verified the field now contains "
                            f"\"{actual[:80]}\".")
                return (f"Error: fill of {cand} did not stick — field "
                        f"contains \"{actual[:80]}\" instead of the intended "
                        "text. Try browser_click first or browser_eval.")
            # Last resort: by visible label/placeholder text.
            try:
                loc = page.get_by_label(selector, exact=False).first
                loc.fill(text, timeout=3_000)
                actual = loc.input_value()
                if actual == text:
                    return (f"Filled field labelled '{selector}'; verified "
                            f"it now contains \"{actual[:80]}\".")
            except Exception:  # noqa: BLE001
                pass
            return (f"Error: no fillable field matched '{selector}' "
                    f"(tried {', '.join(tried)}, label text). Use the exact "
                    "name= or id= from browser_goto's Form fields list.")
        return _with_page(do)

    def browser_screenshot() -> str:
        """Screenshot the current page to workspace/screenshots (+ vision)."""
        def do(page):
            out_dir = Path(settings.simon_workspace_dir) / "screenshots"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"browser-{int(time.time())}.png"
            page.screenshot(path=str(path), full_page=False)
            note = (
                f"Screenshot saved to {path} (use read_file/vision-capable "
                "follow-up to view it)."
            )
            description = describe_image(str(path), settings)
            if description:
                return f"{note}\nDescription: {description}"
            return note
        return _with_page(do)

    def browser_eval(js: str) -> str:
        """Evaluate JavaScript on the current page; returns JSON (max 4000 chars)."""
        def do(page):
            result = page.evaluate(js)
            try:
                rendered = json.dumps(result, default=str)
            except Exception:  # noqa: BLE001
                rendered = str(result)
            return rendered[:_TEXT_CAP]
        return _with_page(do)

    def browser_scroll(direction: str = "down") -> str:
        """Scroll the current page up or down by one viewport."""
        def do(page):
            delta = page.evaluate("() => window.innerHeight")
            sign = -1 if direction.strip().lower() in ("up", "top") else 1
            page.mouse.wheel(0, sign * int(delta))
            return f"Scrolled {'up' if sign < 0 else 'down'}."
        return _with_page(do)

    def browser_tabs() -> str:
        """List open tabs (index: title — url)."""
        try:
            with _lock:
                context = _get_context(settings)
                lines = [
                    f"{i}: {p.title()} — {p.url}"
                    for i, p in enumerate(context.pages)
                ]
            return "\n".join(lines) if lines else "No tabs open."
        except Exception as exc:  # noqa: BLE001
            _reset_context()
            return f"Error: browser action failed: {exc}"

    def browser_close_tab() -> str:
        """Close the active tab."""
        def do(page):
            page.close()
            return "Closed tab."
        return _with_page(do)

    def reg(tool_name, description, properties, func, required=None):
        registry.register(Tool(
            name=tool_name,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
            func=func,
        ))

    reg(
        "browser_goto",
        "Open a URL in Simon's dedicated browser. Returns the page title and "
        "visible text. The browser stays logged in between sessions.",
        {"url": {"type": "string", "description": "URL to navigate to"}},
        browser_goto, ["url"],
    )
    reg(
        "browser_click",
        "Click a page element by visible text or CSS selector.",
        {"selector_or_text": {"type": "string", "description": "visible text or CSS selector"}},
        browser_click, ["selector_or_text"],
    )
    reg(
        "browser_type",
        "Type text into a form field identified by a CSS selector.",
        {
            "selector": {"type": "string", "description": "CSS selector of the input"},
            "text": {"type": "string", "description": "text to enter"},
        },
        browser_type, ["selector", "text"],
    )
    reg(
        "browser_screenshot",
        "Screenshot the current page. Returns a saved PNG path (and a text "
        "description if SIMON_COMPUTER_VISION is enabled).",
        {},
        browser_screenshot,
    )
    reg(
        "browser_eval",
        "Evaluate JavaScript on the current page and return the JSON result.",
        {"js": {"type": "string", "description": "JavaScript expression"}},
        browser_eval, ["js"],
    )
    reg(
        "browser_scroll",
        "Scroll the current page one viewport up or down.",
        {"direction": {"type": "string", "description": "'down' (default) or 'up'"}},
        browser_scroll,
    )
    reg(
        "browser_tabs",
        "List open tabs in Simon's browser (index, title, URL).",
        {},
        browser_tabs,
    )
    reg(
        "browser_close_tab",
        "Close the active browser tab.",
        {},
        browser_close_tab,
    )
    log.info("browser tools registered.")
