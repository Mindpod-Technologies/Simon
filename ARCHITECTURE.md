# ARCHITECTURE.md — How Simon works, top to bottom

*Written 2026-09-04 by Kimi, verified against the live installation on this Mac.
Companion file: `CHANGES.md` (local modifications and tuning).*

---

## The big picture

```
        YOU / FAMILY
   ┌────────┬─────────┬──────────┐
   Slack   Telegram   Web UI     (voice CLI, Teams later)
   └──┬─────┴────┬────┴───┬──────┘
      │          │        │         interfaces/  ← "ears & mouths"
      ▼          ▼        ▼
      ┌──────────────────────┐
      │   agent.py  (brain    │  ← agent loop, model routing, tool dispatch
      │   stem + router)      │
      └───┬────────┬─────────┘
          ▼        ▼
     llm.py    tools/ (24 tools)     ← "thinking" & "hands"
      │          │
      ▼          ▼
   Ollama    this Mac / internet
   (8B fast / 30B-A3B MoE smart)

   memory.py (SQLite)  ← long-term memory, history, reminders
   scheduler.py        ← proactive jobs (07:30 briefing, reminders)
```

One process, started by launchd at login, runs everything via `run.py all`.

---

## 1. Boot: `run.py` (the front door)

**Chain:** macOS login → launchd (`~/Library/LaunchAgents/com.simon.assistant.plist`)
→ `~/simon/.venv/bin/python run.py all`

`run.py` does three things in order:

1. **License check** — `simon/licensing.py` → `require_license_or_exit()`.
   This machine runs trial mode (no key needed); Simon exits only if
   `SIMON_REQUIRE_LICENSE=true` with a bad key.
2. **Config load** — `simon/config.py` → `get_settings()`: pydantic reads `.env`
   once, caches it (`@lru_cache`), and every other module receives this one
   `Settings` object. **`.env` is the single source of truth** — no config is
   hardcoded anywhere else.
3. **`cmd_all()`** spins up one asyncio loop containing: the web server (always),
   the scheduler (always), and each chat interface **only if its tokens exist** —
   e.g. `TELEGRAM_BOT_TOKEN not set; Telegram interface disabled.` in the logs is
   feature-detection via config, not an error.

Modes: `server` (web only), `telegram`, `slack`, `teams`, `voice`, `all`.

---

## 2. A message's full journey (Slack DM example)

**Step 1 — Ears.** `interfaces/slack_bot.py` holds a permanent websocket to Slack
(Socket Mode). A DM arrives → `is_allowed(user_id)` checks the sender against
`SLACK_ALLOWED_USER_IDS`. Refused → logged and ignored (this is how user IDs are
harvested for the allowlist). Allowed → continue.

**Step 2 — Brain stem.** `Agent.handle(text)` in `simon/agent.py`:

- `memory.add_message()` — the message is persisted to SQLite *first*
- `_build_messages()` — assembles the prompt: `persona.py`'s system prompt (with
  today's date injected) + last 40 history messages from `memory.get_history()`
  + the new message
- **Router** — `llm.route_model()`: checks the message against
  `memory.search_facts()` plus complexity heuristics → picks the 8B (fast) or
  27B (smart) model *(local addition — see CHANGES.md §7)*

**Step 3 — The loop** (max `MAX_ITERATIONS = 5`):

```
Agent → llm.chat(messages, tools, model)
      → llm.py → HTTP → Ollama → model replies: text OR tool_call(s)
      → if tool_call: registry.call(name, args) in tools/__init__.py
      → tool result appended to messages → loop again
      → if plain text: done
```

Escalation: 2+ failed tool calls on the fast model → the rest of the turn is
re-run on the smart model automatically.

**Step 4 — Mouth.** Reply text goes back through `slack_bot.py` →
`_reply_with_voice()`: text posted immediately, then `voice/tts.py`
(edge-tts, British `en-GB-RyanNeural`, cloud synthesis) → mp3 uploaded as an
attachment (requires the Slack `files:write` scope).

**Step 5 — Remember.** `memory.add_message(session, "assistant", reply)` — the
exchange becomes part of future context.

Every interface follows this identical pattern — Telegram, web UI and voice CLI
are just different "ears and mouths" around the same `Agent`.

---

## 3. The modules, one line each

| File | Role | Think of it as |
|---|---|---|
| `run.py` | Entry point; picks which interfaces run | The front door |
| `simon/config.py` | `.env` → typed `Settings` object | The fuse box |
| `simon/persona.py` | System prompt: character + tool discipline + safety rules | Simon's personality |
| `simon/agent.py` | Conversation loop, tool dispatch, model routing & escalation | The brain stem |
| `simon/llm.py` | OpenAI-compatible HTTP client + router heuristics | The voice box to the brain |
| `simon/memory.py` | SQLite: chat history, facts (`remember_fact`/`recall_facts`), reminders | Long-term memory |
| `simon/tools/__init__.py` | `ToolRegistry` — schemas out to the LLM; `call()` never raises | The toolbox |
| `simon/tools/builtin.py` | Core tools: web_search, files, notes, calculator, datetime, joke… | Standard equipment |
| `simon/tools/browser.py`, `azure_tool.py`, `subagent_tools.py`, `computer.py`, `email_tool.py`, `calendar_tool.py`, `homeassistant.py` | Conditionally registered per `.env` flags | Optional equipment |
| `simon/voice/tts.py` / `stt.py` | edge-tts (British voice, cloud) / faster-whisper (local speech-to-text) | Mouth & ears |
| `simon/scheduler.py` | APScheduler: 07:30 morning briefing + reminder polling every 30 s | The alarm clock |
| `simon/interfaces/web.py` | FastAPI: `GET /` (chat page), `POST /api/chat` (SSE), `/api/tts`, `/api/stt` | The web telephone |
| `simon/interfaces/slack_bot.py` | Slack Socket Mode bot, text + voice-note replies | The Slack telephone |
| `simon/interfaces/telegram_bot.py` | Telegram long-polling bot (text + voice notes in/out) | The Telegram telephone |
| `simon/interfaces/teams_bot.py` | Bot Framework on `TEAMS_PORT` (needs public HTTPS) | The Teams telephone |
| `simon/interfaces/voice_cli.py` | Walk-up mode: record → STT → agent → TTS → play | The intercom |
| `simon/licensing.py` | Trial vs commercial key check | The lock (open here) |
| `simon/subagents.py` | Child agents for parallel work (depth cap 1, max 3 concurrent) | Simon's interns |
| `plugins/*.py` | Drop-in user tools; loaded if they define `register(registry)` | Third-party attachments |

---

## 4. The two patterns that make it robust

**Everything is config-gated.** `build_default_registry()` registers email tools
only if `IMAP_HOST` is set, Azure only if `SIMON_AZURE_ENABLED`, computer-use
only if opted in — each wrapped in try/except so a broken integration can't kill
startup. Same pattern for interfaces in `run.py`.
*Adding a feature = adding `.env` keys, never editing code.*

**Nothing is allowed to crash the loop.** `ToolRegistry.call()` catches all
exceptions and returns `"Error: ..."` as a string the model can read — Simon
sees his own tool failure and can apologise or retry. The escalation hook in
`agent.py` watches for exactly two such errors and switches to the 27B mid-task.

---

## 5. Local modifications in this flow (details in CHANGES.md)

- `simon/llm.py` — `classify_turn()` + `route_model()` router;
  `reasoning_effort:"none"` for Ollama (hidden thinking disabled)
- `simon/agent.py` — per-turn routing, tool-error escalation, loop cap 8 → 5
- `simon/persona.py` — 2-search hard cap, "answer from expertise" discipline
- `web/static/app.js` — CRLF SSE stream fix (replies render in the web UI)
- `simon/config.py` — `llm_model_fast`, `llm_router_enabled` settings

---

## 6. Data & state locations

| What | Where |
|---|---|
| Conversations, facts, reminders | `~/simon/data/simon.db` (SQLite) |
| Logs | `~/simon/data/logs/simon.err.log` / `simon.out.log` |
| Generated voice notes | `~/simon/data/tts/` |
| Browser automation profile | `~/simon/data/browser-profile/` |
| Config | `~/simon/.env` |
| Models | `~/.ollama/models/` (8B fast ~5 GB + 30B-A3B MoE smart ~15 GB) |
| Auto-start | `~/Library/LaunchAgents/com.simon.assistant.plist` |
| Ollama service | `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist` |

Useful commands:

```bash
launchctl kickstart -k gui/$(id -u)/com.simon.assistant   # restart Simon
tail -f ~/simon/data/logs/simon.err.log                   # watch logs
ollama ps                                                 # which brain is loaded
```

---

## Architecture Decisions

Record of significant "why we do it this way" decisions, so future changes
start from the reasoning rather than re-litigating it.

### ADR-1: No agent framework (LangChain / LangGraph / CrewAI) — 2026-09-09

**Decision:** Simon's core stays hand-rolled. No framework adoption.

**Context:** Simon already implements what those frameworks provide, in
~1,500 lines of owned code: agent tool loop (`agent.py`), model router
(`llm.py`), memory + RAG (`memory.py`, `rag.py`), multi-agent spawning
(`subagents.py`), and local observability (`obs.py` + portal on :8789).

**Reasoning:**
1. *The guardrails depend on owning the pipeline.* The honesty intercept,
   regeneration guard, and date-anchoring rules (see CHANGES.md §15–16) work
   because every message is assembled line-by-line in code we control. As
   framework middleware they would fight the framework's assumptions.
   Abliterated local models need these deterministic guards; frameworks
   don't provide them, being tuned for well-behaved cloud models.
2. *Dependency cost.* LangChain + CrewAI pull in dozens of packages with a
   history of breaking changes — inherited weight for zero new capability.
3. *Debuggability.* This week's degenerate-reply bug was found because the
   event log shows model/route/latency per turn. Through framework
   abstraction layers the same bug is archaeology.
4. *Data locality.* LangSmith-style observability ships conversations to a
   third-party cloud; Simon's portal is local by design.

**Revisit trigger:** if sub-agent orchestration grows into parallel agents
with task dependencies, retries, branching workflows, and approval gates —
the one thing LangGraph does meaningfully better. If that day comes, adopt
LangGraph *for the sub-agent subsystem only*, never as a wholesale rewrite.
Per-feature evaluation, à la carte — not as a foundation.

### ADR-2: Two-brain local model strategy — 2026-09-09

**Decision:** fast 8B for simple turns, 30B-A3B MoE for complex turns,
selected by heuristic router (`llm.py`).

**Constraint discovered:** this Mac (24 GB unified memory) has a hard
per-model ceiling of ~16 GB file size. Anything larger exceeds the Metal
working-set budget (~18 GB) and crashes decode with
`kIOGPUCommandBufferCallbackErrorOutOfMemory` (observed with 27B q4_K_M).
Within any dense 27B family, q3_K-class is the best that fits.

**Reasoning:** the 30B-A3B MoE (30B total / 3B active) delivers 27B-class
knowledge at ~7–17× the decode speed of a dense 27B at the same memory
footprint — the best quality-per-wait available on this hardware.
Abliterated variants throughout, per the owner's requirement.

**Revisit trigger:** a hardware upgrade (≥36 GB unified memory) re-opens the
dense 27B q4_K_L / q5_K class, or a 32B dense model — re-run the A/B
harness from CHANGES.md §17 before switching.
