# Simon

**Simon** is a self-hosted, JARVIS-style personal AI worker. He speaks with a
British accent (`en-GB-RyanNeural`), runs an LLM-driven agent loop with
pluggable tools, and answers on Telegram, a local voice CLI, and a web chat
UI. He lives on a Mac Mini (launchd) or a Linux VPS (Docker or systemd) and is
reached securely over Tailscale.

## Features

- **Agent loop** — any OpenAI-compatible LLM (OpenAI, Anthropic via
  `base_url`, Ollama, OpenRouter) with tool calling, up to 8 tool steps per turn.
- **British voice** — `edge-tts` (default `en-GB-RyanNeural`) for speech out;
  `faster-whisper` runs speech-to-text locally, with an optional OpenAI
  Whisper API fallback (`STT_MODEL=openai`).
- **Five interfaces** — Telegram bot (text + voice notes in/out), Slack bot
  (Socket Mode), Microsoft Teams bot (Bot Framework), a microphone-driven
  voice CLI (Mac), and a dark JARVIS-style web chat page with SSE
  streaming, mic input, and TTS playback.
- **Built-in tools** — web search (DuckDuckGo), shell (off by default),
  workspace-confined file read/write, date/time, calculator, notes, and
  persistent remember/recall facts. Optional: email (IMAP/SMTP), Google
  Calendar (ICS), Home Assistant.
- **Memory** — SQLite stores conversation history, long-term facts, and
  user reminders.
- **Proactive** — APScheduler delivers a 07:30 morning briefing and fires due
  reminders through Telegram.
- **Plugins** — drop a `.py` file into `plugins/` and its tools register
  automatically.

## Architecture

```
                +-------------------+
                |   LLM endpoint    |  (OpenAI-compatible)
                +---------+---------+
                          |
 Telegram ──┐             |
            │    +--------v---------+       +----------------+
 Voice CLI ─┼──> |      Agent       |─────> |  ToolRegistry  |── builtin tools
            │    |  (agent.py loop) |       +----------------+── plugins/*.py
  Web UI ───┘    +--+-----+------+--+
                    |     |      |
              +-----v-+ +-v----+ +v----------+
              | Memory| | Voice| | Scheduler |
              | SQLite| | TTS/ | | briefing, |
              |       | | STT  | | reminders |
              +-------+ +------+ +-----------+
```

## Quickstart (local dev)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit: set LLM_API_KEY
python run.py server        # web UI on http://localhost:8788
```

Other modes: `python run.py telegram`, `python run.py slack`,
`python run.py teams`, `python run.py voice` (Mac only),
`python run.py all` (web + every configured chat interface + scheduler — the production mode).

Tests: `python -m pytest tests`

## Configuration

All settings live in `.env` (see `.env.example`):

| Key | Default | Description |
| --- | --- | --- |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint (Ollama, OpenRouter, …) |
| `LLM_API_KEY` | — | API key for the LLM endpoint (**required**) |
| `LLM_MODEL` | `gpt-4o-mini` | Model name to chat with |
| `TTS_VOICE` | `en-GB-RyanNeural` | edge-tts voice |
| `TTS_RATE` | `+0%` | edge-tts speaking rate (e.g. `+10%`) |
| `STT_MODEL` | `base` | faster-whisper model size (`tiny`/`base`/`small`/…); `openai` = Whisper API |
| `TELEGRAM_BOT_TOKEN` | — | Bot token from BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | — | Comma-separated Telegram user IDs; **empty = deny all** |
| `SLACK_BOT_TOKEN` | — | Slack bot token (`xoxb-…`) |
| `SLACK_APP_TOKEN` | — | Slack app-level token (`xapp-…`, Socket Mode) |
| `SLACK_ALLOWED_USER_IDS` | — | Comma-separated Slack user IDs; **empty = deny all** |
| `TEAMS_APP_ID` | — | Azure Bot / app registration client ID |
| `TEAMS_APP_PASSWORD` | — | App registration client secret |
| `TEAMS_ALLOWED_USER_IDS` | — | Comma-separated AAD object IDs / Teams user IDs; **empty = deny all** |
| `TEAMS_PORT` | `3978` | Port for the Teams bot endpoint |
| `SIMON_PUBLIC_BASE_URL` | — | Public base URL (e.g. `https://simon.example.com`); enables TTS audio attachments in Teams |
| `IMAP_HOST` / `IMAP_USER` / `IMAP_PASSWORD` | — | Email reading (tool enabled only if set) |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` | — | Email sending (tool enabled only if set) |
| `GOOGLE_CALENDAR_ICS` | — | Secret ICS URL for read-only calendar |
| `HOMEASSISTANT_URL` / `HOMEASSISTANT_TOKEN` | — | Home Assistant REST API |
| `SIMON_ALLOW_SHELL` | `false` | Enable the shell tool (dangerous — see SECURITY) |
| `SIMON_WORKSPACE_DIR` | `./workspace` | Sandbox root for file tools |

## Telegram setup

1. Open Telegram, talk to **@BotFather** → `/newbot`, pick a name/username →
   copy the token into `TELEGRAM_BOT_TOKEN`.
2. Get your numeric user ID from **@userinfobot** and put it in
   `TELEGRAM_ALLOWED_USER_IDS` (comma-separated for several people).
3. `python run.py telegram` (or `all`) and message your bot. Voice messages
   are transcribed; replies come back as text plus a voice note.

## Slack setup (Socket Mode — no public endpoint needed)

1. Create an app at <https://api.slack.com/apps> (from scratch), pick your
   workspace.
2. Under **Socket Mode**, enable it and create an app-level token with the
   `connections:write` scope → copy it into `SLACK_APP_TOKEN` (`xapp-…`).
3. Under **OAuth & Permissions**, add the bot scopes `chat:write`,
   `files:write`, `im:history`, `app_mentions:read`; under **Event
   Subscriptions** subscribe to the bot events `app_mention` and
   `message.im`.
4. **Install to workspace** → copy the bot token into `SLACK_BOT_TOKEN`
   (`xoxb-…`).
5. Find your Slack user ID (Profile → ⋯ → Copy member ID) and put it in
   `SLACK_ALLOWED_USER_IDS`.
6. `python run.py slack` (or `all`). DM the bot or @-mention it in a
   channel; replies come back as text plus a voice-note mp3.

## Microsoft Teams setup

Teams requires a **publicly reachable HTTPS endpoint** for the Bot
Framework — Tailscale Funnel, Caddy with valid TLS, or an Azure host all
work.

1. In the Azure Portal create an **Azure Bot** resource (and the linked
   **app registration**); copy the app ID into `TEAMS_APP_ID` and create a
   client secret for `TEAMS_APP_PASSWORD`.
2. Expose Simon: e.g. `tailscale funnel --bg 3978` or a Caddy reverse
   proxy to `TEAMS_PORT` (default `3978`).
3. Set the Azure Bot's **messaging endpoint** to
   `https://<your-host>/api/messages`.
4. Build a Teams app in the **Developer Portal for Teams** (manifest with
   your bot's app ID, personal/team scopes), install it, and enable the
   **Microsoft Teams channel** on the Azure Bot resource.
5. Put your AAD object ID (or Teams user ID) in `TEAMS_ALLOWED_USER_IDS`.
6. Set `SIMON_PUBLIC_BASE_URL=https://<your-host>` so voice notes are
   attached as audio links (served from `/api/ttsfile/{name}`); without it
   replies are text-only.
7. `python run.py teams` (or `all`).

## Voice notes

- TTS defaults to **en-GB-RyanNeural**. Other good British voices:
  `en-GB-SoniaNeural`, `en-GB-LibbyNeural`, `en-GB-ThomasNeural`
  (list all: `edge-tts --list-voices | grep en-GB`).
- The voice CLI (`python run.py voice`) needs a microphone, `sounddevice`
  (portaudio), and a player (`afplay` on macOS) — it's intended for the Mac
  Mini, not the VPS container.
- First STT run downloads the faster-whisper model; `STT_MODEL=openai` skips
  local STT entirely and uses your `LLM_API_KEY` against the Whisper API.

## Mac Mini deployment

```bash
git clone <this-repo> ~/simon
~/simon/deploy/install_mac.sh
```

The idempotent script: installs `python@3.11`, `ffmpeg`, `portaudio` via
Homebrew (installs brew instructions first if missing), creates `.venv`,
installs dependencies, copies `.env.example` → `.env` (edit it!), creates
`data/logs`, renders `deploy/com.simon.assistant.plist` into
`~/Library/LaunchAgents/` (replacing the `__HOME__` placeholder), and
`launchctl load`s it. Simon then starts at login and is restarted on crash
(`RunAtLoad` + `KeepAlive`).

Keep the Mini serving around the clock:

- **Stay awake**: `sudo pmset -a sleep 0` (on power), or Amphetamine, or
  `caffeinate -d` for a session.
- **Auto-login**: System Settings → Users & Groups → Automatic login, so the
  LaunchAgent comes up after a reboot.
- Logs: `~/simon/data/logs/simon.out.log` / `.err.log`.
- Restart after editing `.env`:
  `launchctl kickstart -k gui/$(id -u)/com.simon.assistant`

## VPS deployment

```bash
git clone <this-repo> && cd simon
deploy/install_vps.sh              # Docker (default)
deploy/install_vps.sh --systemd    # bare-metal systemd instead
```

- **Docker (default)** — installs Docker if missing, creates `.env`, then
  `docker compose -f deploy/docker-compose.yml up -d --build`. One container
  runs `python run.py all` (Telegram + web on `8788` + scheduler); SQLite data
  persists in the `simon-data` volume.
- **systemd** — installs `python3.11` + `ffmpeg`, rsyncs the repo to
  `/opt/simon`, builds a venv, and enables `deploy/simon.service`
  (`journalctl -u simon -f` for logs).

The voice CLI is Mac-only; it is not part of the VPS container.

## Remote access

Use **Tailscale** (see `deploy/tailscale.md`): install it on the Mac Mini,
VPS, and your phone, then open `http://<tailscale-ip>:8788` (or the MagicDNS
name). No open ports, WireGuard encryption, works behind CGNAT. Optional:
`tailscale serve 8788` for HTTPS, or Caddy + basic auth if you must be public.

## Azure management

Simon can manage your Azure tenant by shelling out to the **Azure CLI** (`az`).
Simon never stores Azure credentials — it uses whatever identity `az` is
logged in as on the host.

**Install the CLI:**
- macOS: `brew install azure-cli`
- Debian/Ubuntu: `curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash`

**Authenticate per host:**
- **Mac (personal):** `az login` (interactive browser).
- **VPS that is an Azure VM:** enable a *system-assigned managed identity* on
  the VM, then `az login --identity`.
- **VPS elsewhere:** create a service principal and log in with it:
  `az login --service-principal -u <appId> -p <secret> --tenant <tenantId>`.

**Least privilege (important):**
- Monitoring-only: create a dedicated SP and assign **Reader** (+ *Cost
  Management Reader* for `azure_costs`) at subscription scope:
  `az ad sp create-for-rbac --name simon-reader --role Reader --scopes /subscriptions/<subId>`
- Only if you want VM start/stop: additionally assign **Virtual Machine
  Contributor** (ideally scoped to specific resource groups, not the whole
  subscription).

**Enable in `.env`:**
```
SIMON_AZURE_ENABLED=true        # read-only tools: account, resources, RGs, VM status, costs, Entra, health
SIMON_AZURE_ALLOW_WRITE=false   # true also registers azure_vm_start / azure_vm_stop / azure_vm_restart
```

**Example prompts:** "Simon, which VMs are running?", "what did we spend this
month?", "list everything in rg-prod", "who is in the 'Data Team' group?",
"restart web-vm in rg-prod" (write).

**Safety:** `azure_vm_stop` *deallocates* the VM (stops compute billing).
All write tools are registered only when `SIMON_AZURE_ALLOW_WRITE=true`, and
Simon's persona rules require it to confirm with you before any write action.

## Sub-agents

Simon can **spawn child sub-agents** that work on tasks in parallel in
background threads, then report back. Just ask, e.g.:

> "Simon, research the best NAS drives and the current Mac Mini prices in
> parallel."

Simon will call `spawn_agent` once per piece of work, keep chatting with you,
and check in with `agent_result` when the children finish. Manage them with
`list_agents`, `agent_status`, and `cancel_agent`.

- **Depth cap 1** — sub-agents get the full tool registry *minus* the
  sub-agent tools themselves, so a child can never spawn grandchildren. This
  keeps fan-out bounded and predictable.
- **Concurrency** — at most `SIMON_SUBAGENTS_MAX_CONCURRENT` (default 3)
  sub-agents run at once; extras stay queued until a slot frees.
- **Cost warning** — every sub-agent is a full agent loop with its own LLM
  calls, so each one burns tokens. Prefer sub-agents for genuinely parallel
  or long-running work, not for trivial questions.
- Disable entirely with `SIMON_SUBAGENTS_ENABLED=false`.

## Computer control (Mac)

Opt-in (`SIMON_COMPUTER_USE=false` by default), macOS only — the tools simply
aren't registered elsewhere. Simon can take screenshots, click/type, open apps
and URLs, inspect windows, and run AppleScript on the Mac he runs on.

1. `brew install --cask cliclick` — required for mouse clicks/moves (typing
   and key presses fall back to AppleScript).
2. Grant the app that runs Simon (Terminal, iTerm, or the launchd agent)
   permissions in **System Settings → Privacy & Security**:
   - **Accessibility** — mouse/keyboard control
   - **Screen Recording** — `screencapture`
   - **Automation** — prompted per target app on first AppleScript use
3. Set `SIMON_COMPUTER_USE=true` in `.env`. Optionally set
   `SIMON_COMPUTER_VISION=true` to have the LLM describe screenshots (needs a
   vision-capable `LLM_MODEL`).

**Safety posture:** off by default; AppleScript and typing can do anything the
owner can, so Simon's persona is confirm-first before consequential actions.

## Dedicated browser

Enabled by default (`SIMON_BROWSER_ENABLED=true`). Simon drives his own
Playwright Chromium — separate from your personal browser.

```bash
pip install playwright
playwright install chromium
```

- The profile persists at `./data/browser-profile`, so **log in to a site once
  and Simon stays logged in** across restarts.
- Headless by default; set `SIMON_BROWSER_HEADLESS=false` to watch him work in
  a visible window.
- Tools: `browser_goto`, `browser_click`, `browser_type`, `browser_scroll`,
  `browser_tabs`, `browser_close_tab`, `browser_eval`, `browser_screenshot`
  (vision-described when `SIMON_COMPUTER_VISION=true`).

## Security

- **Telegram allowlist is mandatory** — `TELEGRAM_ALLOWED_USER_IDS` empty
  denies everyone.
- **`SIMON_ALLOW_SHELL` is `false` by default.** The shell tool executes real
  commands; only enable it if you trust everyone who can reach Simon.
- File tools are confined to `SIMON_WORKSPACE_DIR`.
- The web UI has **no authentication** and binds `0.0.0.0:8788` — keep it on
  your tailnet (Tailscale) or behind Caddy basic auth; never expose it bare.
- The systemd unit sets `NoNewPrivileges`, `ProtectSystem=full`, `PrivateTmp`.
- Secrets live only in `.env` (git-ignored).

## Plugins

Drop a file in `plugins/` — it is auto-loaded at startup:

```python
# plugins/dice.py
from simon.tools import tool, Tool

def register(registry):
    @tool(name="roll_dice", description="Roll an N-sided die",
          parameters={"type": "object",
                      "properties": {"sides": {"type": "integer", "default": 6}},
                      "required": []})
    def roll(sides: int = 6) -> str:
        import random
        return f"You rolled a {random.randint(1, sides)} (d{sides})."
    registry.register(roll)
```

A plugin module just needs a `register(registry)` function; use the `@tool`
decorator to build `Tool`s. See `plugins/example_plugin.py`.

## Troubleshooting

- **`ffmpeg` missing** — voice notes fall back to raw mp3 and the voice CLI
  can't play audio. macOS: `brew install ffmpeg`; Debian/Ubuntu:
  `apt install ffmpeg`. (The Docker image includes it.)
- **Telegram bot silent** — check `TELEGRAM_BOT_TOKEN` (from BotFather, no
  spaces) and that your user ID is in `TELEGRAM_ALLOWED_USER_IDS`; the bot
  ignores everyone else by design.
- **First run hangs on STT** — faster-whisper downloads the model
  (~75 MB for `base`) on first use; subsequent runs are instant. Or set
  `STT_MODEL=openai`.
- **TTS fails** — `edge-tts` streams from Microsoft's service; it needs
  outbound network access. Check connectivity and that the voice name in
  `TTS_VOICE` is valid (`edge-tts --list-voices`).
- **Web UI unreachable** — confirm the process is `run.py server`/`all`, the
  port is 8788, and (on a VPS) that you're connecting over Tailscale rather
  than the public IP.

## Commercial use / licensing

Simon is source-available: **personal and evaluation use is free** — no key
needed, nothing to configure. **Commercial use** (providing Simon to others
as a paid product or service) requires a purchased license key:

```bash
SIMON_LICENSE_KEY=SIMON-pro-...   # key from purchase
SIMON_REQUIRE_LICENSE=true        # refuse startup without a valid key
```

Keys are Ed25519-signed and verified fully offline — no phone-home, no
telemetry. Without a key (or even without the `cryptography` package
installed) Simon simply runs as the free `trial` plan. See
[COMMERCIAL.md](COMMERCIAL.md) for tiers, fulfillment, and white-label
notes, [LICENSE](LICENSE) for the legal text, and `tools/keygen.py` for the
vendor-side key generator (private key — never ship to customers).
