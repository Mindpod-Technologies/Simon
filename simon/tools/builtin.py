"""Built-in tools for Simon: datetime, calculator, web search, confined file
access, optional shell, notes, and long-term fact memory."""
from __future__ import annotations

import ast
import logging
import operator
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .base import Tool

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- datetime

def _get_datetime() -> str:
    now = datetime.now().astimezone()
    return now.strftime("%A, %d %B %Y, %H:%M:%S %Z").strip()


# -------------------------------------------------------------- calculator

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_eval(node):
    """Evaluate an arithmetic AST node. No names, calls, or builtins allowed."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError("only numbers are allowed")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("exponent too large")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError(f"unsafe or unsupported expression: {ast.dump(node)}")


def _calculator(expression: str) -> str:
    tree = ast.parse(expression, mode="eval")
    result = _safe_eval(tree)
    return str(result)


# -------------------------------------------------------------- web search

def _web_search(query: str) -> str:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return "Error: duckduckgo-search package is not installed"
    try:
        results = list(DDGS().text(query, max_results=5))
    except Exception as exc:  # noqa: BLE001 - network/search failures are non-fatal
        log.warning("web_search failed: %s", exc)
        return f"Error: web search failed: {exc}"
    if not results:
        return "No results found."
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        snippet = r.get("body", "")
        link = r.get("href", "")
        lines.append(f"{i}. {title}\n   {snippet}\n   {link}")
    return "\n".join(lines)


# --------------------------------------------------------------- fetch URL

_FETCH_MAX_CHARS = 6000


def _fetch_url(url: str) -> str:
    """Fetch a web page over plain HTTP and extract readable text.

    Lightweight alternative to the full Playwright browser: fast, and works
    even when the browser binaries are unavailable. Not suitable for
    JavaScript-rendered pages — the tool says so when it gets little text.
    """
    if not re.match(r"^https?://", url or ""):
        url = "https://" + (url or "").lstrip("/")
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return "Error: requests/beautifulsoup4 are not installed"
    try:
        resp = requests.get(
            url, timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Simon-Assistant)"})
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - network failures are non-fatal
        log.warning("fetch_url failed for %s: %s", url, exc)
        return f"Error: could not fetch {url}: {exc}"
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer",
                     "nav", "form"]):
        tag.decompose()
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines()
                     if line.strip())
    if len(text) < 200:
        return (f"Fetched {url} but extracted very little text "
                f"({len(text)} chars) — the page is probably "
                f"JavaScript-rendered. Use browser_goto instead.\n\n{text}")
    note = ""
    if len(text) > _FETCH_MAX_CHARS:
        text = text[:_FETCH_MAX_CHARS]
        note = f"\n\n[truncated at {_FETCH_MAX_CHARS} characters]"
    return f"Content fetched from {url}:\n\n{text}{note}"


# ------------------------------------------------- workspace-confined files

def _workspace_root(settings) -> Path:
    root = Path(settings.simon_workspace_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_in_workspace(settings, path: str) -> Path:
    root = _workspace_root(settings)
    target = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if target != root and root not in target.parents:
        raise PermissionError(f"path '{path}' escapes the workspace directory")
    return target


def _read_file(settings, path: str) -> str:
    target = _resolve_in_workspace(settings, path)
    if not target.is_file():
        return f"Error: no such file in workspace: {path}"
    return target.read_text(encoding="utf-8", errors="replace")


def _write_file(settings, path: str, content: str) -> str:
    target = _resolve_in_workspace(settings, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {target.relative_to(_workspace_root(settings))}"


def _list_files(settings, path: str = ".") -> str:
    target = _resolve_in_workspace(settings, path)
    if not target.is_dir():
        return f"Error: no such directory in workspace: {path}"
    entries = sorted(
        f"{p.name}/" if p.is_dir() else p.name for p in target.iterdir()
    )
    return "\n".join(entries) if entries else "(empty directory)"


# -------------------------------------------------------------------- shell

def _run_shell(command: str) -> str:
    proc = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    out = out[:4000]
    return f"exit code {proc.returncode}\n{out}" if out else f"exit code {proc.returncode}"


# -------------------------------------------------------------------- notes

def _notes_path(settings) -> Path:
    return _workspace_root(settings) / "notes.txt"


def _take_note(settings, note: str) -> str:
    path = _notes_path(settings)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(note.rstrip("\n") + "\n")
    return "Noted."


def _read_notes(settings) -> str:
    path = _notes_path(settings)
    if not path.exists():
        return "(no notes yet)"
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------- fact memory

def _remember_fact(key: str, value: str) -> str:
    from simon import memory

    memory.set_fact(key, value)
    return f"Remembered: {key}"


def _recall_facts(query: str) -> str:
    from simon import memory

    facts = memory.search_facts(query)
    if not facts:
        return "I don't have any facts matching that."
    return "\n".join(f"{k}: {v}" for k, v in facts)


# -------------------------------------------------------------- registration

def register_builtin_tools(registry, settings) -> None:
    """Register the always-on builtin tools (shell only if SIMON_ALLOW_SHELL)."""
    registry.register(Tool(
        name="get_datetime",
        description="Get the current date and time.",
        parameters={"type": "object", "properties": {}, "required": []},
        func=_get_datetime,
    ))
    registry.register(Tool(
        name="calculator",
        description="Evaluate a basic arithmetic expression (+-*/, %, //, **, parentheses).",
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Arithmetic expression, e.g. '2 * (3 + 4)'"}
            },
            "required": ["expression"],
        },
        func=_calculator,
    ))
    registry.register(Tool(
        name="web_search",
        description="Search the web (DuckDuckGo). Returns the top 5 results with titles, snippets and links.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
        func=_web_search,
    ))
    registry.register(Tool(
        name="fetch_url",
        description=(
            "Fetch the text content of a web page over HTTP. Use this FIRST "
            "when the user gives a URL or asks about a specific page — "
            "faster and more reliable than the full browser. If the page is "
            "JavaScript-rendered (little text extracted), use browser_goto "
            "instead."),
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string",
                                   "description": "The URL to fetch "
                                                  "(scheme optional)"}},
            "required": ["url"],
        },
        func=_fetch_url,
    ))
    registry.register(Tool(
        name="read_file",
        description="Read a text file inside the Simon workspace directory.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relative to the workspace"}},
            "required": ["path"],
        },
        func=lambda path: _read_file(settings, path),
    ))
    registry.register(Tool(
        name="write_file",
        description="Write text content to a file inside the Simon workspace directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace"},
                "content": {"type": "string", "description": "Text content to write"},
            },
            "required": ["path", "content"],
        },
        func=lambda path, content: _write_file(settings, path, content),
    ))
    registry.register(Tool(
        name="list_files",
        description="List files and directories inside the Simon workspace directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory relative to the workspace (default '.')", "default": "."}
            },
            "required": [],
        },
        func=lambda path=".": _list_files(settings, path),
    ))
    if getattr(settings, "simon_allow_shell", False):
        registry.register(Tool(
            name="run_shell",
            description="Run a shell command (30s timeout). Output truncated to 4000 chars. Use with caution.",
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string", "description": "Shell command to execute"}},
                "required": ["command"],
            },
            func=_run_shell,
        ))
    registry.register(Tool(
        name="take_note",
        description="Append a note to the persistent notes file.",
        parameters={
            "type": "object",
            "properties": {"note": {"type": "string", "description": "The note text to save"}},
            "required": ["note"],
        },
        func=lambda note: _take_note(settings, note),
    ))
    registry.register(Tool(
        name="read_notes",
        description="Read all saved notes.",
        parameters={"type": "object", "properties": {}, "required": []},
        func=lambda: _read_notes(settings),
    ))
    registry.register(Tool(
        name="remember_fact",
        description="Store a fact about the user or world in long-term memory.",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Short fact key, e.g. 'favourite_drink'"},
                "value": {"type": "string", "description": "The fact value, e.g. 'Earl Grey tea'"},
            },
            "required": ["key", "value"],
        },
        func=_remember_fact,
    ))
    registry.register(Tool(
        name="recall_facts",
        description="Search long-term memory for facts matching a query.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search text"}},
            "required": ["query"],
        },
        func=_recall_facts,
    ))
