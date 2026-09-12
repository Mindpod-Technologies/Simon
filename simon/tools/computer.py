"""macOS computer-use tools for Simon (opt-in, macOS only).

Lets Simon see and control the owner's Mac: screenshots, mouse, keyboard,
opening apps/URLs, window inspection, and arbitrary AppleScript.

Registered by build_default_registry() only when SIMON_COMPUTER_USE=true
AND the platform is macOS (darwin).

PERMISSIONS: the app running Simon (Terminal, iTerm, or the launchd agent)
must be granted, in System Settings → Privacy & Security:
  * Accessibility     — required for mouse/keyboard control (cliclick,
                        System Events keystrokes)
  * Screen Recording  — required for `screencapture` to capture content
  * Automation        — prompted per target app on first AppleScript use
Clicks require `cliclick` (brew install --cask cliclick); typing and keys
fall back to AppleScript via osascript.
"""
from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .base import Tool
from .vision import describe_image

log = logging.getLogger(__name__)

_TIMEOUT = 30

_CLICLICK_HINT = (
    "Mouse clicks require 'cliclick' (brew install --cask cliclick). "
    "Install it, then retry."
)

# macOS virtual key codes for osascript `key code`.
_KEY_CODES = {
    "return": 36, "enter": 36, "tab": 48, "escape": 53, "esc": 53,
    "space": 49, "delete": 51, "backspace": 51,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
}

_MODIFIER_MAP = {
    "cmd": "command down", "command": "command down",
    "shift": "shift down",
    "alt": "option down", "option": "option down",
    "ctrl": "control down", "control": "control down",
}


def _run(argv: list[str], timeout: int = _TIMEOUT) -> str:
    """Run a command, returning stdout or an error string. Never raises."""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            return f"Error: {argv[0]} exited {proc.returncode}: {err or out or 'no output'}"
        return out or "Done."
    except subprocess.TimeoutExpired:
        return f"Error: {argv[0]} timed out after {timeout}s"
    except FileNotFoundError:
        return f"Error: command not found: {argv[0]}"
    except Exception as exc:  # noqa: BLE001 - tools must never raise
        return f"Error: {exc}"


def _osascript(script: str, timeout: int = _TIMEOUT) -> str:
    return _run(["osascript", "-e", script], timeout=timeout)


def _quote_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _keystroke_script(key: str) -> str | None:
    """Build an AppleScript for a key press, e.g. 'return' or 'cmd+space'.

    Returns None if the key name is not recognised.
    """
    parts = [p.strip().lower() for p in key.split("+") if p.strip()]
    if not parts:
        return None
    mods = [m for p in parts[:-1] if (m := _MODIFIER_MAP.get(p))]
    plain = [p for p in parts if p not in _MODIFIER_MAP]
    if len(plain) != 1:
        return None
    base = plain[0]
    using = f" using {{{', '.join(mods)}}}" if mods else ""
    if base in _KEY_CODES and not mods:
        body = f"key code {_KEY_CODES[base]}"
    elif base in _KEY_CODES:
        # combos like cmd+tab are fine as key code with modifiers
        body = f"key code {_KEY_CODES[base]}{using}"
    elif len(base) == 1:
        body = f'keystroke "{base}"{using}'
    else:
        return None
    return (
        'tell application "System Events" to '
        f"{body}"
    )


def _screenshots_dir(settings) -> Path:
    path = Path(settings.simon_workspace_dir) / "screenshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def register_computer_tools(registry, settings) -> None:
    """Register macOS computer-use tools (darwin + SIMON_COMPUTER_USE only)."""
    if not getattr(settings, "simon_computer_use", False):
        log.info("computer-use tools skipped: SIMON_COMPUTER_USE is not enabled")
        return
    if platform.system() != "Darwin":
        log.info(
            "computer-use tools skipped: platform %r is not macOS",
            platform.system(),
        )
        return

    cliclick = shutil.which("cliclick")

    def computer_screenshot() -> str:
        """Capture the screen to a PNG and describe it if vision is enabled."""
        tmp = Path(tempfile.gettempdir()) / f"simon-screen-{int(time.time())}.png"
        result = _run(["screencapture", "-x", str(tmp)])
        if result.startswith("Error:"):
            return result
        keep = _screenshots_dir(settings) / tmp.name
        try:
            shutil.copy2(tmp, keep)
        except Exception as exc:  # noqa: BLE001
            return f"Error: screenshot taken but copy to workspace failed: {exc}"
        note = (
            f"Screenshot saved to {keep} (use read_file/vision-capable "
            "follow-up to view it)."
        )
        description = describe_image(str(keep), settings)
        if description:
            return f"{note}\nDescription: {description}"
        return note

    def computer_click(x: int, y: int) -> str:
        """Left-click at screen coordinates (requires cliclick)."""
        if not cliclick:
            return f"Error: cannot click. {_CLICLICK_HINT}"
        return _run([cliclick, f"c:{x},{y}"])

    def computer_double_click(x: int, y: int) -> str:
        """Double-click at screen coordinates (requires cliclick)."""
        if not cliclick:
            return f"Error: cannot double-click. {_CLICLICK_HINT}"
        return _run([cliclick, f"dc:{x},{y}"])

    def computer_move(x: int, y: int) -> str:
        """Move the mouse pointer to screen coordinates (requires cliclick)."""
        if not cliclick:
            return f"Error: cannot move mouse. {_CLICLICK_HINT}"
        return _run([cliclick, f"m:{x},{y}"])

    def computer_type(text: str) -> str:
        """Type text into the focused app via AppleScript keystrokes."""
        script = (
            'tell application "System Events" to keystroke '
            f'"{_quote_applescript(text)}"'
        )
        return _osascript(script)

    def computer_key(key: str) -> str:
        """Press a key or combo: return, tab, escape, space, arrows, cmd+space..."""
        script = _keystroke_script(key)
        if script is None:
            return (
                f"Error: unrecognised key '{key}'. Use names like return, tab, "
                "escape, space, left/right/up/down, delete, or combos such as "
                "'cmd+space', 'cmd+shift+4'."
            )
        return _osascript(script)

    def computer_open_app(name: str) -> str:
        """Open (or focus) an application by name, e.g. 'Safari'."""
        return _run(["open", "-a", name])

    def computer_open_url(url: str) -> str:
        """Open a URL in the default browser."""
        return _run(["open", url])

    def computer_applescript(script: str) -> str:
        """Run arbitrary AppleScript and return its output."""
        return _osascript(script)

    def computer_active_app() -> str:
        """Return the name of the frontmost application."""
        return _osascript(
            'tell application "System Events" to get name of '
            "first process whose frontmost is true"
        )

    def computer_list_windows() -> str:
        """List window titles of visible applications."""
        script = (
            'tell application "System Events"\n'
            "set out to \"\"\n"
            "repeat with p in (every process whose visible is true)\n"
            "set out to out & (name of p) & \": \"\n"
            "repeat with w in (every window of p)\n"
            "set out to out & (name of w) & \" | \"\n"
            "end repeat\n"
            "set out to out & linefeed\n"
            "end repeat\n"
            "return out\n"
            "end tell"
        )
        return _osascript(script)

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

    _xy = {
        "x": {"type": "integer", "description": "screen X coordinate (pixels)"},
        "y": {"type": "integer", "description": "screen Y coordinate (pixels)"},
    }

    reg(
        "computer_screenshot",
        "Take a screenshot of the Mac's screen. Returns a saved PNG path "
        "(and a text description if SIMON_COMPUTER_VISION is enabled).",
        {},
        computer_screenshot,
    )
    reg(
        "computer_click",
        "Left-click at screen coordinates (x, y). Requires cliclick.",
        _xy, computer_click, ["x", "y"],
    )
    reg(
        "computer_double_click",
        "Double-click at screen coordinates (x, y). Requires cliclick.",
        _xy, computer_double_click, ["x", "y"],
    )
    reg(
        "computer_move",
        "Move the mouse pointer to screen coordinates (x, y). Requires cliclick.",
        _xy, computer_move, ["x", "y"],
    )
    reg(
        "computer_type",
        "Type text into whatever application is currently focused.",
        {"text": {"type": "string", "description": "text to type"}},
        computer_type, ["text"],
    )
    reg(
        "computer_key",
        "Press a key or key combo, e.g. 'return', 'tab', 'escape', 'space', "
        "'left', 'cmd+space', 'cmd+tab'.",
        {"key": {"type": "string", "description": "key name or combo"}},
        computer_key, ["key"],
    )
    reg(
        "computer_open_app",
        "Open or focus a macOS application by name, e.g. 'Safari', 'Mail'.",
        {"name": {"type": "string", "description": "application name"}},
        computer_open_app, ["name"],
    )
    reg(
        "computer_open_url",
        "Open a URL in the default browser.",
        {"url": {"type": "string", "description": "URL to open"}},
        computer_open_url, ["url"],
    )
    reg(
        "computer_applescript",
        "Run arbitrary AppleScript and return its output. POWERFUL and "
        "potentially destructive (can control any app, delete files, etc.) — "
        "always confirm with the owner before consequential actions.",
        {"script": {"type": "string", "description": "AppleScript source"}},
        computer_applescript, ["script"],
    )
    reg(
        "computer_active_app",
        "Return the name of the currently focused (frontmost) application.",
        {},
        computer_active_app,
    )
    reg(
        "computer_list_windows",
        "List window titles of all visible applications.",
        {},
        computer_list_windows,
    )
    log.info("computer-use tools registered (cliclick=%s)", cliclick or "missing")
