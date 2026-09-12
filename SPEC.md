# SPEC.md — SIMON: Personal AI Worker (JARVIS-style)

**Simon** is a self-hosted personal AI assistant. British-accented voice, LLM-driven agent loop, pluggable tools, multiple interfaces (Telegram, CLI voice, web chat), runs on a Mac Mini (launchd) or Linux VPS (Docker/systemd).

## Tech Stack
- Python 3.11+, FastAPI + Uvicorn (web), python-telegram-bot v21 (Telegram), edge-tts (British TTS), faster-whisper (local STT) with optional OpenAI Whisper API fallback, SQLite (memory + jobs), APScheduler (proactive jobs), pydantic-settings (config), openai SDK (works with OpenAI, Anthropic via base_url, Ollama, OpenRouter — any OpenAI-compatible endpoint).
- No frontend build tooling: web UI is a single static HTML page with vanilla JS + SSE.

## Repo Layout (exact)
```
simon/
├── README.md
├── SPEC.md                      (copy of this file)
├── requirements.txt
├── .env.example
├── pyproject.toml               (minimal, optional install)
├── run.py                       (entry: python run.py [server|telegram|voice|all])
├── simon/
│   ├── __init__.py              (__version__ = "1.0.0")
│   ├── config.py                (pydantic-settings Settings, loads .env)
│   ├── llm.py                   (OpenAI-compatible chat client w/ tool calling)
│   ├── agent.py                 (agent loop: messages, tool dispatch, system prompt w/ Simon persona)
│   ├── memory.py                (SQLite: conversations, facts, jobs)
│   ├── persona.py               (SIMON_SYSTEM_PROMPT — JARVIS-like British butler persona)
│   ├── voice/
│   │   ├── __init__.py
│   │   ├── tts.py               (edge-tts, default voice en-GB-RyanNeural; say(text)->mp3 path)
│   │   └── stt.py               (faster-whisper local; transcribe(audio_path)->str)
│   ├── tools/
│   │   ├── __init__.py          (ToolRegistry, @tool decorator, load_plugins)
│   │   ├── base.py              (Tool dataclass: name, description, parameters(JSON schema), func)
│   │   ├── builtin.py           (web_search[duckduckgo], shell, read/write file, datetime, calculator, notes, remember/recall facts)
│   │   ├── email_tool.py        (IMAP read + SMTP send, enabled only if configured)
│   │   ├── calendar_tool.py     (Google Calendar via service account or ICS read; optional)
│   │   └── homeassistant.py     (REST call to HA; optional)
│   ├── interfaces/
│   │   ├── __init__.py
│   │   ├── telegram_bot.py      (run_telegram(settings): text + voice-note in/out)
│   │   ├── voice_cli.py         (run_voice_cli(settings): record→STT→agent→TTS→play)
│   │   └── web.py               (FastAPI app: / (chat page), /api/chat (SSE), /api/tts, static/)
│   └── scheduler.py             (APScheduler: morning briefing, user reminders stored in SQLite)
├── plugins/                     (user drop-in plugins; example_plugin.py demonstrating @tool)
│   └── example_plugin.py
├── web/
│   └── static/
│       ├── index.html           (dark, minimal JARVIS-style chat UI, mic button via WebAudio)
│       └── app.js
├── deploy/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── simon.service            (systemd unit for VPS)
│   ├── com.simon.assistant.plist(macOS launchd)
│   ├── install_mac.sh           (brew deps, venv, launchctl load)
│   ├── install_vps.sh           (apt deps, docker compose up -d OR systemd)
│   └── tailscale.md             (secure remote access notes)
└── tests/
    ├── test_tools.py
    ├── test_memory.py
    └── test_agent.py            (agent loop with a FakeLLM — no network)
```

## Configuration (.env.example — exact keys)
```
# LLM (any OpenAI-compatible endpoint)
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
# Voice
TTS_VOICE=en-GB-RyanNeural
TTS_RATE=+0%
STT_MODEL=base            # faster-whisper model size; or 'openai' to use Whisper API
# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_IDS=   # comma-separated; empty = deny all
# Optional integrations
IMAP_HOST= IMAP_USER= IMAP_PASSWORD=
SMTP_HOST= SMTP_USER= SMTP_PASSWORD=
GOOGLE_CALENDAR_ICS=          # secret ICS URL for read-only calendar
HOMEASSISTANT_URL= HOMEASSISTANT_TOKEN=
# Safety
SIMON_ALLOW_SHELL=false       # shell tool disabled unless true
SIMON_WORKSPACE_DIR=./workspace
```

## Interface Contracts

### simon/config.py
```python
class Settings(BaseSettings):
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    tts_voice: str = "en-GB-RyanNeural"
    tts_rate: str = "+0%"
    stt_model: str = "base"
    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""
    imap_host: str = ""; imap_user: str = ""; imap_password: str = ""
    smtp_host: str = ""; smtp_user: str = ""; smtp_password: str = ""
    google_calendar_ics: str = ""
    homeassistant_url: str = ""; homeassistant_token: str = ""
    simon_allow_shell: bool = False
    simon_workspace_dir: str = "./workspace"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
def get_settings() -> Settings  # cached
```

### simon/tools/base.py + __init__.py
```python
@dataclass
class Tool:
    name: str; description: str; parameters: dict  # JSON schema
    func: Callable[..., str]                        # returns string result
class ToolRegistry:
    def register(self, tool: Tool) -> None
    def schemas(self) -> list[dict]                 # OpenAI tools format
    def call(self, name: str, args: dict) -> str    # never raises; returns error string
def tool(name, description, parameters)            # decorator
def build_default_registry(settings) -> ToolRegistry  # builtin + optional + plugins/*.py
```

### simon/llm.py
```python
class LLM:
    def __init__(self, settings): ...
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict
    # returns {"content": str|None, "tool_calls": [{"id","name","arguments": dict}]}
```

### simon/agent.py
```python
class Agent:
    def __init__(self, settings, registry=None, session_id="default"): ...
    def handle(self, user_text: str) -> str
    # loop: append user msg → LLM.chat → if tool_calls: execute via registry, append results, repeat (max 8) → return final content
    # persists conversation via memory.py
```

### simon/memory.py (SQLite at ./data/simon.db)
```python
init_db(path); add_message(session_id, role, content); get_history(session_id, limit=40)
set_fact(key, value); get_fact(key); search_facts(query) -> list[tuple[str,str]]
add_reminder(text, run_at_iso); due_reminders(now_iso) -> list; delete_reminder(id)
```

### simon/voice/tts.py + stt.py
```python
async def synthesize(text: str, out_path: str, voice: str, rate: str) -> str  # edge-tts
def say(text: str, settings) -> str   # sync wrapper, returns mp3 path under ./data/tts/
def transcribe(audio_path: str, settings) -> str  # faster-whisper; if stt_model=="openai" use API
```

### Interfaces
- `run_telegram(settings)`: long-polling bot; only allowed user IDs; text in → Agent.handle → text out + voice note (TTS mp3→ogg via ffmpeg if available, else mp3 file); voice message in → STT → handle.
- `run_voice_cli(settings)`: loop: record (sounddevice, Enter to stop) → transcribe → handle → say → afplay/ffplay.
- Web app `create_app(settings) -> FastAPI`: `GET /` serves web/static/index.html; `POST /api/chat` {text} → SSE stream of reply chunks; `POST /api/tts` {text} → mp3; `POST /api/stt` (multipart audio) → {text}.

### simon/scheduler.py
```python
class Scheduler:  # APScheduler AsyncIOScheduler
    def __init__(self, settings, agent_factory, notify: Callable[[str], None])
    def start(self)  # morning briefing at 07:30 + polls due_reminders every 30s
```

### run.py
`python run.py server` (uvicorn web on 0.0.0.0:8788) | `telegram` | `voice` | `all` (server+telegram+scheduler in one process via asyncio).

## Persona (persona.py)
SIMON_SYSTEM_PROMPT: "You are Simon, a highly capable personal AI assistant in the tradition of JARVIS — polite, dry British wit, addresses the user as 'sir' occasionally, concise, proactive. You have tools; use them when helpful. Today is {date}." Include tool-usage guidance + safety: confirm before destructive shell commands.

## Safety
- Shell tool off by default (SIMON_ALLOW_SHELL); workspace-confined file tools; Telegram user allowlist mandatory; web UI binds 0.0.0.0 but docs recommend Tailscale/Caddy auth.

## requirements.txt (exact deps)
```
fastapi uvicorn[standard] openai pydantic-settings python-dotenv
edge-tts faster-whisper sounddevice soundfile numpy
python-telegram-bot>=21 apscheduler duckduckgo-search
requests ics beautifulsoup4 sse-starlette pytest
```

## Tests (no network)
- test_tools: registry register/call, calculator, notes roundtrip, unknown tool error string
- test_memory: facts + reminders CRUD with tmp db
- test_agent: FakeLLM that issues one tool_call then final answer; assert tool executed and reply returned

## Done criteria
All files present, `python -m pytest tests` passes in a clean venv, README has Mac Mini + VPS deploy sections, deploy scripts syntactically valid (bash -n), Dockerfile builds conceptually (static review).
