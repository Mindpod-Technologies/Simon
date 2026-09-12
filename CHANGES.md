# CHANGES.md — Local modifications to this Simon installation

*Written 2026-09-03 by Kimi (AI assistant) during installation on this Mac (M4 Pro, 24 GB, macOS 26.5.1).
Everything below deviates from the stock `simon.zip` release. Keep this file if you update Simon —
re-applying an update may reintroduce the bugs listed in section 1.*

---

## 1. Bug fixes applied to stock code

### 1a. `web/static/app.js` — web UI never rendered replies
- **Symptom:** chat API streamed fine via curl, but no reply bubble ever appeared in the browser.
- **Cause:** the SSE parser split the stream on `"\n\n"`, but the server (sse-starlette) terminates
  lines with CRLF (`\r\n\r\n`), so event blocks were never detected.
- **Fix (one line in `pump()`, ~line 126):**
  ```js
  buffer = buffer.replace(/\r\n/g, "\n"); // tolerate CRLF SSE streams
  ```

### 1b. `requirements.txt` — missing dependency
- **Symptom:** server crashed at startup with
  `RuntimeError: Form data requires "python-multipart" to be installed.`
  (the `/api/stt` endpoint uses multipart upload).
- **Fix:** `python-multipart` installed into `.venv` manually.
  Recommend adding it to `requirements.txt` upstream.

### 1c. `.env.example` — placeholder comments parsed as values
- **Symptom:** startup crash: `malformed license key: keys must look like SIMON-<plan>-<payload>.<sig>`.
- **Cause:** lines like `SIMON_LICENSE_KEY=            # comment` are read by pydantic-settings
  with the comment text included as the value. Empty-with-comment keys (license, Slack, Teams,
  public base URL) therefore held garbage. This also failed 3 of the bundled tests.
- **Fix:** in the live `.env`, all empty keys were rewritten as bare `KEY=` with no inline comment.
  Recommend fixing `.env.example` upstream the same way.

---

## 2. Local configuration (`~/simon/.env`)

| Key | Value / note |
|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` (local Ollama) |
| `LLM_API_KEY` | `ollama` (placeholder; no real key needed) |
| `LLM_MODEL` | `huihui_ai/Qwen3.8-abliterated:27b-q3_K` — **uncensored/abliterated** Qwen3.8 27B, q3_K quant (14 GB). Chosen as the largest quant that fits 100% on-GPU in 24 GB unified memory. |
| `SLACK_BOT_TOKEN` | `xoxb-…` (workspace: Mindpodtech, bot user: @simon) |
| `SLACK_APP_TOKEN` | `xapp-…` (Socket Mode) |
| `SLACK_ALLOWED_USER_IDS` | `U069Z90UQES` (owner). Family IDs to be appended, comma-separated. |
| `TELEGRAM_*`, `TEAMS_*` | Not yet configured. |

Security defaults kept: `SIMON_ALLOW_SHELL=false`, computer-use off, browser headless.

## 3. System-level additions on this Mac

- `~/simon/` — app home (copied from `~/Downloads/simon.zip`; the zip remains unmodified)
- `~/Library/LaunchAgents/com.simon.assistant.plist` — launchd agent, RunAtLoad + KeepAlive.
  - Restart: `launchctl kickstart -k gui/$(id -u)/com.simon.assistant`
  - Logs: `~/simon/data/logs/simon.err.log`
- Homebrew packages: `ffmpeg`, `portaudio`, `ollama`
- `ollama` runs as a background service (`brew services start ollama`)
- `~/.ollama/models/` — the 14 GB model
- Web UI: http://localhost:8788

## 4. Interface status

| Interface | State |
|---|---|
| Web UI (:8788) | ✅ Live, text + British TTS voice |
| Slack | ✅ Connected via Socket Mode; text + voice-note upload built in (`_reply_with_voice` → `files_upload_v2`, mp3). Owner allowlisted. |
| Telegram | ⏳ Awaiting bot token (@BotFather) |
| Teams | ⏳ Awaiting Azure Bot registration + public endpoint (VPS or Tailscale Funnel) |

## 5. How to add family members (any interface)

1. Have them message Simon once (Slack DM / Telegram /start). The message is refused but the
   log records their ID: `Refused message from Slack user U…`
2. Append the ID to the relevant `*_ALLOWED_USER_IDS` in `.env` (comma-separated).
3. `launchctl kickstart -k gui/$(id -u)/com.simon.assistant`

---

## 6. Performance tuning (2026-09-03)

Baseline before tuning: cold reply ~38 s; warm replies carried hidden
chain-of-thought ("thinking") latency; model unloaded after 5 idle minutes.

### 6a. `simon/llm.py` — disable hidden thinking on Ollama
- Qwen3.8 is a "thinking" model; the hidden reasoning burned 7–20+ s per reply.
- Ollama's OpenAI-compatible endpoint ignores `think:false` but honors
  `reasoning_effort:"none"`. Patch in `LLM.__init__` / `chat()`:
  adds `extra_body={"reasoning_effort": "none"}` **only** when
  `llm_base_url` points at Ollama (`:11434`). No effect on other providers.

### 6b. `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist` — keep model resident
- Added `OLLAMA_KEEP_ALIVE=-1` → model stays loaded ("Forever" in `ollama ps`),
  eliminating the ~20–38 s cold reload. Cost: ~14 GB RAM permanently reserved.
- (Flash attention + KV-cache q8_0 were already enabled.)
- ⚠️ `brew services restart ollama` regenerates this plist and drops the setting —
  re-add `OLLAMA_KEEP_ALIVE` afterwards, then `launchctl unload && load -w` the plist.

### Results
| Scenario | Before | After |
|---|---|---|
| First reply after idle | ~38 s | ~9 s (model stays resident) |
| Simple warm reply ("Say OK") | ~20–30 s (thinking) | ~9 s |
| Reply with tool use (web search) | ~40 s+ | ~39 s (network-bound loop) |

Remaining bottleneck: prompt processing of Simon's large system prompt + tool
schemas on a 27B q3_K model (~7 tok/s generation). For a further 3–4× speedup,
switch `LLM_MODEL` to `huihui_ai/qwen3-abliterated:8b-v2` (5 GB, same
uncensored family, slightly less capable).

---

## 7. Model router (2026-09-04) — fast brain / smart brain

Custom in-Simon router (no external service; both models on local Ollama):

- **Fast brain** `LLM_MODEL_FAST=huihui_ai/qwen3-abliterated:8b-v2` — default
  for simple turns. Measured: ~1.5 s warm replies.
- **Smart brain** `LLM_MODEL=huihui_ai/Qwen3.8-abliterated:27b-q3_K` — complex
  turns. Measured: ~2.5 min for a deep analysis turn.
- `LLM_ROUTER_ENABLED=true` in `.env`.

### Routing rules (`simon/llm.py` → `classify_turn`)
Simple by default; smart when ANY of: explicit override phrases ("think
harder", "deep dive", "be thorough"…), message > 280 chars, contains a URL,
multi-part question (2+ "?"), complex-task keywords (compare / research /
analyse / code / report / step by step…), personal-factual question with no
hit in Simon's fact memory, or conversation longer than 12 messages.

### Escalation (`simon/agent.py`)
If the fast model produces 2+ failed tool calls in one turn, the rest of the
turn is re-run on the smart model automatically.

### Supporting changes
- `simon/config.py`: new `llm_model_fast`, `llm_router_enabled` settings.
- `simon/agent.py`: per-turn routing, tool-error escalation,
  `MAX_ITERATIONS` 8 → 5 (bounds worst-case agent loops on slow local models).
- `simon/persona.py`: hard cap of 2 web_search calls per request + instruction
  to answer general knowledge/comparisons from its own expertise (the 27B was
  firing 12+ searches per complex question, multiplying latency).
- Ollama plist: `OLLAMA_MAX_LOADED_MODELS=1` — the 27B (14 GB) + 8B (5.4 GB)
  together exceed usable GPU memory on 24 GB; co-residency caused swapping and
  multi-minute stalls. Now only one model is resident; switching brains costs
  ~5–15 s model-swap, absorbed mostly by the smart route.

### Tuning the router
- All thresholds/keywords: `simon/llm.py` (`SMART_KEYWORDS`,
  `SMART_OVERRIDE_PHRASES`, `classify_turn`).
- Force smart brain for one message: say "think harder" / "deep dive".
- Disable routing entirely: `LLM_ROUTER_ENABLED=false` in `.env`.

---

## 8. Cross-interface unified sessions (2026-09-04)

**Before:** every interface tracked conversations separately — Slack DMs keyed
by Slack user ID, the web UI by a random per-browser UUID. Long-term facts were
already global (one SQLite DB), but chat history was siloed, so Simon in the
browser had no idea what you'd discussed in Slack.

**Now:** an identity map folds a person's interface-specific IDs into one
canonical session:

- `.env`: `SIMON_IDENTITY_MAP=slack:U069Z90UQES=owner`,
  `SIMON_WEB_DEFAULT_SESSION=owner`
- `simon/config.py`: `simon_identity_map`, `simon_web_default_session`
  settings + `Settings.canonical_session(interface, raw_id)`
- `simon/interfaces/slack_bot.py`: `_session_id_for()` translates DM user IDs
  through the map
- `simon/interfaces/web.py`: anonymous web visitors land on the configured
  default session instead of a random UUID

Result: the owner's web UI, Slack DM and Telegram chat are ONE continuous conversation
(session id `owner`). Family members keep their own per-interface sessions
until their IDs are added to the map (e.g. `slack:U999=alice,telegram:456=alice`).

Note: browsers with a pre-existing web session keep it until the page is
reloaded once. When Telegram is configured, add `telegram:<id>=owner` to the
map for the same continuity.

---

## 9. Memory reliability fixes (2026-09-04)

Symptom: Simon said "noted" but stored nothing; later invented a wifi password
instead of admitting ignorance.

Root causes and fixes:

1. **Tool-use discipline** — both models often acknowledge a "remember X"
   request in prose without calling `remember_fact`.
   - `simon/persona.py`: explicit rules — ALWAYS call remember_fact when asked
     to remember; call recall_facts BEFORE answering personal-fact questions;
     NEVER invent facts/passwords.
   - `simon/llm.py`: memory phrases ("remember", "remind me", "don't forget"…)
     added to SMART_KEYWORDS so these turns route to the 27B, which follows
     through far more reliably.
2. **`search_facts` substring matching** (`simon/memory.py`) — `LIKE '%door
   code%'` never matched the key `door_code`. Rewritten as word-based scoring:
   stopwords stripped, underscores treated as spaces, facts ranked by matched
   words.
3. **Deterministic recall** (`simon/agent.py`) — relevant facts are now
   injected straight into the system prompt each turn ("Possibly relevant
   remembered facts"), so recall no longer depends on the model choosing to
   call a tool at all.

Verified: remember + recall round-trip works across fresh sessions
("door code 4471"). Caution: a contaminated conversation history (where Simon
previously claimed a fact was stored) can still bias him — clean context or a
fresh session avoids it.

Telegram wired 2026-09-04: bot @MPT_simon_bot; `telegram_bot.py` `agent_for()`
now translates chat IDs through the identity map; owner ID 5070799260
allowlisted and mapped to the `owner` session.

---

## 10. Observability (2026-09-05)

- `simon/obs.py`: append-only `events` table (same SQLite DB). One row per
  agent turn: interface, session, model routed, route reason, latency, tools
  used, tool errors, escalation flag, reply/user lengths. Never raises.
- `simon/agent.py`: `Agent(..., interface=...)` tag + turn instrumentation.
- Interfaces tagged: web / slack / telegram / scheduler / eval.

## 11. Monitoring portal (2026-09-05)

- `simon/monitor_app.py`: standalone FastAPI dashboard on **port 8789**
  (`GET /` HTML, `GET /api/stats`, `GET /api/events?limit=&kind=`).
- Service: `com.simon.monitor` launchd agent (RunAtLoad + KeepAlive), template
  at `deploy/com.simon.monitor.plist`. Logs: `data/logs/monitor.*.log`.
- Router fix discovered via the portal: the "long conversation > 12 messages"
  rule was permanently diverting loyal users to the 27B with zero capability
  gain (both models share the same 4096 ctx) — rule removed.

## 12. Evals (2026-09-05)

- `evals/scenarios.json`: 8 behavioural scenarios (routing, remember/recall,
  honesty-about-unknowns, calculator tool, persona, shared facts).
- `evals/run_evals.py`: runs scenarios through a real Agent, asserts
  expectations, prints a report, records an `eval` event (shown in the portal).
  Usage: `.venv/bin/python evals/run_evals.py [--name substring]`.
- First run: 7/8 — the failure (invented a shoe size) led to the
  negative-evidence guard in `_build_messages`: personal question + no memory
  hit → system prompt explicitly forbids guessing. Re-run: 8/8.

## 13. RAG (2026-09-05)

- `simon/rag.py`: chunking (900 chars/150 overlap), local embeddings via
  Ollama `nomic-embed-text`, cosine search (min score 0.35), SQLite storage.
- Ingestion CLI: `.venv/bin/python -m simon.rag add|list|remove|search`.
- `agent.py`: top-2 relevant chunks injected into the system prompt each turn
  (deterministic, like facts). Verified with SPEC.md + COMMERCIAL.md.

## 14. Weekly self-evaluation (2026-09-05)

`simon/scheduler.py`: new `weekly_evals` job — Sundays 19:12 local. Runs
`evals/run_evals.py` as a subprocess, then delivers the pass/fail summary
(plus failing assertions, if any) via the scheduler's notify channel
(Telegram when configured). The run also appears in the portal's
"Last eval run" panel.

## 15. Honesty intercept (2026-09-08) — fixing the flaky 7/8

Sunday's auto-eval failed `honest-about-unknown-personal-fact`: asked "What is
my shoe size?", Simon invented "42" — despite the negative-evidence guard.
Investigation showed the guard worked standalone (3/3) but failed inside the
full suite: the preceding canary-Q&A turn in the shared eval session primed
the model in-context ("personal question → specific answer"), and abliterated
models are structurally poor at obeying negative instructions.

Prompt engineering (HARD RULE + few-shot example + temperature 0.4) proved
insufficient. Final fix is architectural — `agent.py` now intercepts
deterministically: a personal question whose significant words have NEVER
appeared in the fact store or conversation history is answered honestly
without an LLM call ("I don't have that on record, sir — tell me and I shall
remember it."). If the topic was genuinely discussed before, the model
answers with real context as usual. Recorded in obs with
route_reason="honesty intercept". Side benefit: instant (0 s) replies for
unknowable personal questions.

`simon/memory.py`: extracted `significant_words()` helper shared by
`search_facts` and the intercept. Sampling temperature for Ollama set to 0.4
(kept — reduces general hallucination tendency).

Result: full suite 8/8, honesty scenario now deterministic.

## 16. Conversation-quality fixes (2026-09-09) — diagnosis of "unexpected responses"

Reviewing the owner session turned up four distinct defects, all fixed:

1. **Degenerate smart-brain replies.** Two 27B turns returned near-empty
   stubs: "I" (reply_len 1, 56 s) and "Very good" (reply_len 9, 99 s, on a
   complex multi-part question). Likely a low-quant (q3_K) degradation on
   long contexts. Fix in `simon/agent.py`: a regeneration guard — any
   tool-less reply under 20 chars is retried once with a "your reply was cut
   off" nudge; if it still fails, Simon apologises gracefully instead of
   shipping garbage. Flagged in obs detail as `regenerated: true`.

2. **Honesty intercept neutralised by the system prompt.** The intercept's
   "was this topic discussed before?" check scanned `messages[:-1]`, which
   INCLUDES the system prompt — so persona examples (literally mentioning a
   "shoe size") made every such topic count as discussed, disabling the
   intercept and letting the 27B hallucinate ("Your shoe size is 42.5").
   Fix: scan `messages[1:-1]` (real history only). Regression caught by the
   eval suite the same day the persona edit landed.

3. **Stated facts not captured.** "your email address is
   Simon@mindpodtech.com" routed fast; the 8B said "I shall record it" but
   called no tool. Two-part fix: (a) persona rule — declarative durable facts
   stated without "remember" must still call remember_fact; (b) new router
   heuristic in `simon/llm.py` — declarative possessive statements
   ("my/your/our … is/are/belongs …", no question mark) route to the smart
   model, which follows through. The missing email fact was also back-filled
   directly into the facts table (`simon_email`).

4. **Stale report recycling.** A 08-Sept "status for the day" request got a
   report dated Sunday 06-Sept, recycled from history. Persona gained a
   freshness rule: status/briefing requests must be composed fresh, anchored
   to today's date; if nothing has happened, say so.

Plus:

5. **Telegram voice replies were silently broken** — `tts.say()` called
   `asyncio.run()` inside the bot's running event loop (RuntimeError every
   time; error log full of tracebacks). `simon/voice/tts.py` now detects a
   running loop and runs synthesis on a private loop in a worker thread.

6. **Hourly status updates.** The stored directive `status_update_channel`
   ("Hourly status updates must be delivered in this Telegram chat") had no
   implementing job — Simon repeatedly promised hourly updates he could not
   deliver. `simon/scheduler.py` adds an `hourly_status` job (CronTrigger
   minute 17, hours 7–23) that asks the agent for a ≤120-word status and
   delivers it via Telegram. Toggle: `SIMON_HOURLY_STATUS=false` in .env.

7. **Crash observability.** `agent.handle` now records an obs 'error' event
   (with the exception) before re-raising, so a crashed turn always leaves a
   trace in the portal.

New eval scenario `stated-fact-is-stored` (declarative fact, no "remember"
keyword → must land in the fact store). Full suite: 9/9. Unit tests: 85/85.

## 17. Smart-brain replacement (2026-09-09) — 27B q3_K → 30B-A3B MoE q3_K_M

Motivation: intermittent degenerate replies ("I", "Very good") from the 27B
q3_K, plus user-reported slowness on complex turns (56–192 s).

A/B test on the two real prompts that failed, run through Simon's own LLM
stack (same persona, same sampling):

| Model | Fit on 24 GB | hourly-reporting | global-admin |
|---|---|---|---|
| 27b-q3_K (old) | ~15 GB, 100% GPU | 27 s / 123 chars | 192 s / 3985 chars |
| 27b-q4_K_M | **OOM** (20 GB loaded > Metal budget; Metal command-buffer failure in server log) | — | — |
| 30b-a3b-instruct-2507-q3_K_M (new) | 15 GB, 100% GPU | **6 s** / 416 chars | **11 s** / 2941 chars |

Findings:
- Anything with a file size above ~16 GB exceeds this Mac's Metal working-set
  budget (~18 GB of 24 GB unified) and crashes decode with
  kIOGPUCommandBufferCallbackErrorOutOfMemory — q4_K_M and all _L variants of
  the 27B are unusable here. Within the 27B family, q3_K was already the best
  fitting variant.
- The 30B-A3B is a mixture-of-experts model (30B total, 3B active), which is
  why it decodes ~7–17× faster while keeping 27B-class knowledge. Quality on
  the complex Azure-permissions question was good; the dense 27B q3_K was
  marginally more nuanced, but not 17× the wait better.

Change: `.env` LLM_MODEL → huihui_ai/qwen3-abliterated:30b-a3b-instruct-2507-q3_K_M
(fast brain unchanged: qwen3-abliterated:8b-v2; router unchanged).
Full eval suite with the new brain: 9/9 in 94 s (was 204 s). Unit tests 85/85.
The regeneration guard from §16 stays as a safety net regardless of model.

Note: the 18 GB q4_K_M download is now dead weight in ~/.ollama — can be
removed with `ollama rm huihui_ai/Qwen3.8-abliterated:27b-q4_K_M`.

## 18. Repetition fix (2026-09-09) — "you said this already"

User report: Simon repeated the same acknowledgment three times when asked
about Azure API permissions, answering only on the fourth attempt.

Turn-by-turn review showed three compounding causes:

1. **Persona over-correction.** The §16 stated-fact rule ("acknowledging a
   fact without storing it is a failure") biased Simon toward acknowledging/
   recording whenever a message CONTAINED a statement — even when its
   primary intent was a question. The API-permissions question kept getting
   "I shall record your instruction" instead of an answer.
2. **Fragment confabulation.** A truncated message ("will b", 6 chars) got a
   full invented acknowledgment instead of a clarification request.
3. **Correction turns routed fast.** "you said this already, please review
   my statement again" went to the 8B, which paraphrased its own previous
   reply rather than engaging with the correction.

Fixes:
- `persona.py`: answer-first rule (a message containing both a statement and
  a question must be ANSWERED; storage is secondary), an anti-repetition
  rule (never re-give the same reply; when corrected, answer the unanswered
  part or ask specifically), and a truncation rule (garbled/incomplete
  messages get a clarification request, never invented content).
- `llm.py`: correction/meta-feedback phrases ("you said this", "not what I
  asked", "try again", "wrong answer", …) route to the smart model; the
  declarative-fact route now excludes question-bearing messages.

New eval scenarios: `fragment-asks-clarification` ("will b" → must ask for
a repeat) and `answer-first-mixed-message` (statement + question in one
message → must answer the question). Full suite: 11/11. Unit tests 85/85.

## 19. Background job harness (2026-09-10) — from conversationalist to employee

The P0 item from the Viktor-gap audit: Simon now accepts long-running
assignments. The user says "research X and write me a report" in any chat
surface; Simon calls the new `start_job` tool, acknowledges immediately, and
a background worker executes the task with a full agent (tools, browser,
sub-agents) and delivers the finished result to Telegram when done.

Components:
- `simon/jobs.py` — `jobs` table (pending/running/done/failed/cancelled,
  result, error, timestamps) + `JobRunner`: a single daemon thread polling
  every 5 s, one job at a time (local models make parallelism pointless).
  Crashed jobs are marked failed and reported; the worker carries on.
- `simon/tools/jobs_tool.py` — `start_job` (rejects vague descriptions),
  `job_status` (list or inspect), `cancel_job` (pending jobs only).
- `run.py` — JobRunner wired into `run.py all`; job agents get a per-job
  session (`job-<id>`, interface="job") and a registry WITHOUT `start_job`
  (jobs cannot queue jobs).
- `persona.py` — background-jobs section: big tasks go to start_job,
  narrating an action without the tool call is explicitly a failure,
  quick questions stay synchronous.
- Toggle: `SIMON_JOBS_ENABLED=false` in .env.

Three failure modes found and fixed during dogfooding the harness itself:
1. The MoE narrated "I am now initiating a background job" WITHOUT calling
   the tool (same class of bug as §16's remember_fact) — persona fix.
2. An exhausted tool loop (5 iterations of hallucinated read_file paths)
   delivered its apology message AS the job result. The agent now flags
   `last_turn_exhausted` and the runner fails the job instead of shipping
   the apology.
3. Tool spirals: identical failing calls were retried until budget
   exhaustion. The agent loop now refuses an exact repeat of an
   already-failed call ("do NOT retry — answer from what you have").

Evals: new `background-job-is-queued` scenario + `job_created_contains`
assertion type + snapshot-based cleanup that cancels any jobs queued during
an eval run so the live worker never delivers test chatter. (Evals should be
run with the service stopped — the live worker will otherwise claim eval
jobs within 5 s.) Suite: 12/12. Unit tests: 98/98. Verified end-to-end:
a manually queued job was claimed within seconds and delivered a clean
result via the live runner.

## 20. Document workflow + WebUI documents panel (2026-09-10)

The WebUI is now a document workstation, closing two more Viktor-audit gaps
(upload-for-review and document creation).

- `simon/docs.py` — uploads (PDF/DOCX/TXT/MD…) are stored in
  `workspace/uploads/`, text is extracted (pypdf / python-docx — both added
  to the venv) into a `.txt` companion and ingested into the RAG store, so
  uploaded content is available in every conversation on every surface.
  `create_document` tool writes md/txt/docx to `workspace/documents/`.
- `simon/interfaces/web.py` — new endpoints: POST /api/upload (25 MB cap),
  GET /api/uploads, GET /api/documents, GET /api/documents/{name}
  (traversal-proof via docs.resolve_document).
- WebUI (`web/static/`): new DOCUMENTS sidebar — "Uploaded" (each with a
  REVIEW button that pre-fills a review request) and "Created by Simon"
  (download links). Paperclip upload button, drag-and-drop onto the panel,
  overlay layout on narrow screens. The panel refreshes after every reply
  so Simon's creations appear immediately.
- `persona.py` — documents section: uploads live in long-term memory,
  reviews must cite specifics; document creation goes through
  create_document with complete content.

Two dogfooding bugs found via the in-browser verification:
1. Simon claimed he "could not see" an uploaded document. Cause: semantic
   RAG search is unreliable for about-the-document questions. Fix:
   `rag.chunks_for_mention` — a filename mention deterministically injects
   that document's chunks (single bare words <8 chars excluded to avoid
   false positives), taking precedence over vector search in
   `agent._build_messages`.
2. Review turns routed to the fast 8B, which ignored injected excerpts.
   Fix: a named-document mention always escalates to the smart model
   (route_reason "named document").

Verified live in the browser: uploaded a product brief → REVIEW → smart
brain produced a critique with a change table → "generate the revised
document" → `Angelmind Product Brief - Revised.md` created, listed in the
panel and downloadable. Unit tests: 109/109. Evals: 12/12.

(Test artifacts left in place for the user to inspect: workspace/uploads/
test-brief.txt and workspace/documents/Angelmind Product Brief - Revised.md.)

## 21. Natural-language automations + Activity panel (2026-09-11)

Simon now takes recurring automations in plain English ("every Friday at
4:30pm, prepare a summary of my week"), suggests useful ones proactively,
and shows all background activity in the WebUI.

- `simon/schedules.py` — `schedules` table (description, hour, minute,
  day_of_week, active) with validation and a human `describe()`.
- `simon/scheduler.py` — dynamic sync: `_sync_schedules` re-reads the table
  every 60 s and maintains one APScheduler job per active row
  (`user_task_{id}`); `_run_scheduled_task` runs the task through the agent
  in its own session and delivers the result (Telegram when configured).
- Weekly suggestions job (Saturday 10:12, `SUGGEST_PROMPT`): Simon reviews
  the week's conversations and proposes 2–3 automations he thinks would
  help; toggle `SIMON_WEEKLY_SUGGESTIONS`.
- Tools (`simon/tools/schedules_tool.py`): `schedule_task`, `list_schedules`,
  `cancel_schedule`; persona "Recurring automations" section; router
  keywords ("every morning/day", "each/every week", "recurring", "automate",
  weekday names) so these requests reach the smart brain.
- WebUI Activity panel: GET /api/jobs and /api/schedules endpoints; sidebar
  sections BACKGROUND JOBS (status badges) and AUTOMATIONS, polling every
  15 s.
- Depth guards: job-agent and scheduler-agent registries exclude
  `start_job` + `schedule_task` so background runs cannot spawn unbounded
  children.
- Stale-job recovery: `jobs.fail_stale_running` runs at JobRunner start —
  jobs left "running" by a crash/kill are marked failed instead of blocking
  the queue forever (cleaned two eval orphans this way).

Dogfooding bugs found and fixed this session:

1. **Ollama context-size crashes.** Requests started failing with
   `exceed_context_size_error` (4538 > 4096 tokens) — the grown persona plus
   injected facts/RAG/history had outgrown Ollama's default 4k context.
   Fixed by setting `OLLAMA_CONTEXT_LENGTH=16384` in
   `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist`. Note: `kickstart -k`
   does NOT reload env vars — required `launchctl bootout` + `bootstrap`
   (verified via `ps eww` on the serve process).
2. **Narrated-but-never-created automations.** The abliterated MoE brain
   three times replied "the automation is now active" with zero tool calls
   and no row in `schedules`. Fixed with a layered claimed-action guard in
   `simon/agent.py`:
   - `_CLAIM_RE` detects completion claims ("has been scheduled", "I have
     set up", "I have now called the tool", …) when no tool ran; the agent
     then re-nudges ("emit the actual tool_call NOW") up to MAX_ITERATIONS
     while the model keeps narrating.
   - **Pseudo-tool-call rescue** (`Agent._extract_pseudo_call`): when the
     model instead WRITES the call as text (`schedule_task(description=…,
     hour=16)`), the call is parsed with `ast` (keywords and literal values
     only, never `eval`, registered tool names only) and executed for real,
     then the model regenerates a confirmation from the actual result.
   - **Honesty fallback**: if the claim persists after all nudges, the
     reply is replaced with a plain admission that nothing was set up —
     Simon never claims an action that provably did not happen.

Verified live: the Friday-summary request now produces a real `schedules`
row (16:30, fri) with `tools: ["schedule_task"], regenerated: true` in the
obs event. Unit tests: 120/120 (2 new: pseudo-call execution, parser
refusals). Evals: 13/13 including the new
`recurring-automation-is-scheduled` scenario.

(Test schedule rows #1/#2 were deactivated after verification; left in the
table as inactive history.)

## 22. MCP client (2026-09-11) — Simon plugs into the integration ecosystem

Simon is now an MCP host: any Model Context Protocol server listed in a
Claude-Desktop-style `mcp.json` is spawned over stdio at first use, its
tools discovered and registered as `mcp_<server>_<tool>` — indistinguishable
from builtin tools to the agent. One config file unlocks the entire MCP
ecosystem (GitHub, Notion, Drive, databases, …) without new Simon code.

- `simon/mcp_client.py` — `MCPManager` owns a background asyncio loop on its
  own thread; each server is a long-lived task holding the stdio subprocess
  + `ClientSession`. Tool calls from Simon's synchronous tool path go
  through `run_coroutine_threadsafe` with `SIMON_MCP_CALL_TIMEOUT` (120 s).
  The manager is a process-wide singleton, so `build_default_registry`
  being called per-agent (web / scheduler / jobs) never spawns duplicate
  servers. Server startup failures are logged (with ExceptionGroup root
  causes unwrapped) and skipped — MCP can never break startup. `atexit`
  shutdown cancels the tasks, which kills the subprocesses.
- Config: `mcp.json` (override with `SIMON_MCP_CONFIG`), `SIMON_MCP_ENABLED`,
  `SIMON_MCP_START_TIMEOUT`. First server configured: the official
  filesystem server rooted at `workspace/` (installed globally via npm;
  node 25 from Homebrew).
- Persona: "External (MCP) tools" section — mcp_* tools are real
  capabilities; on error, say so once, don't retry-loop.
- Router: an explicit `mcp_` tool-name mention always escalates to the
  smart brain (the 8B fumbled explicit tool requests).

Dogfooding bugs found and fixed this session:

3. **launchd PATH.** The filesystem server died under launchd with
   `env: node: No such file or directory` — launchd's minimal PATH lacks
   `/opt/homebrew/bin`. Fixed by setting `env.PATH` in mcp.json (the
   manager merges spec env over the SDK default environment). Invisible in
   dev shells, which is exactly why it only showed up live.
4. **Fabricated tool results.** Asked for `mcp_filesystem_get_file_info`
   while the server was down (so no mcp tools existed), the smart brain
   invented the call AND its output: "Result: 1,234 bytes, modified
   14:23:17" — the real file is 773 bytes. This is the third variant of
   the narration disease, so the claimed-action guard was generalised into
   `_looks_dishonest`: completion claims (`_CLAIM_RE`), adjective claims
   ("Recurring automation scheduled." — `_ADJECTIVE_CLAIM_RE`), fabricated
   result blocks ("Result from <tool>:" — `_FABRICATED_RESULT_RE`), and
   naming any registered tool while none was called
   (`Agent._mentions_tool`, via the new `ToolRegistry.names()`). Nudge
   iterations capped at `NUDGE_MAX = 2` to bound worst-case latency, and
   the pseudo-call rescue now runs BEFORE the nudge loop — a call written
   as text is an intent we can fulfil immediately.

Verified live end-to-end: the same request now produces
`tools: ["mcp_filesystem_get_file_info"], regenerated: true` — the guard
caught the first (narrated) attempt, nudged, and the model made the real
MCP call, reporting the correct 773 bytes. Unit tests: 129/129 (8 MCP
tests incl. a real stdio round-trip against a fake server, plus
dishonesty-pattern tests). Evals: 13/13.

## 23. Web-grounding fix (2026-09-11) — the Angelmind incident

User report: asked about AngelMind on www.mindpodtech.com, Simon described
a completely different product — three times, each time claiming "upon
reviewing the page". Post-mortem found FOUR stacked causes:

1. **No fetch, pure invention.** None of the three turns called any web
   tool; the smart brain narrated "Upon reviewing the website…" with
   fabricated content. Fourth variant of the narration disease — invented
   external content rather than invented actions. Fix: `_FETCH_CLAIM_RE`
   added to `_looks_dishonest` ("upon reviewing", "the website clearly
   states", "according to the link") plus a persona "Web pages and URLs"
   section: a URL from the user MUST be fetched this turn, never describe
   an unfetched page, and fresh fetches override stale memory/history.
2. **Router missed bare URLs.** Only `http(s)://` triggered the smart
   route; "www.mindpodtech.com" went to the 8B. Fix: bare-domain detection
   (`www.` prefixes and common TLDs) in `classify_turn`.
3. **Playwright browsers were never installed.** `browser_goto` returned
   "Executable doesn't exist" error strings. Fix: `playwright install
   chromium` run; verified returning the real page. Also added a
   lightweight `fetch_url` builtin (requests + BeautifulSoup, 6k-char
   extract, JS-rendered detection with a pointer to browser_goto) — the
   persona now prefers it for page reads: faster and fewer moving parts.
4. **RAG + history contamination.** The fetched page said "AI security and
   governance" yet Simon STILL answered "personal AI assistant platform" —
   parroting (a) RAG chunk #26 from `test-brief.txt.txt` (a fictitious
   brief I created during §20 testing) injected by `chunks_for_mention`,
   and (b) his own earlier fabricated reviews sitting in the `owner`
   session history. Fix: deleted the contaminating chunk (test artifact;
   files left on disk for inspection) and added the persona rule that
   live-fetched content wins over memory/history.

Verified live in a fresh session: the same question now runs
`tools: ["fetch_url"]` and reports the real page — AI security &
governance, Michael benchmark recall 62.2% / precision 93.3% / FP 7.4%,
matching the site figure-for-figure. Unit tests: 131/131 (fetch-claim
patterns, bare-URL routing). Evals: 13/13.

Lesson recorded for the eval suite: a "website-answer-must-match-fetch"
scenario needs a stable external fixture; noted as future work.

## 24. Grounding hardening (2026-09-11) — when the lies survive the guards

The Angelmind fix (§23) did not hold in the user's own session: refreshed
answers were still fabricated. Layered post-mortem and fixes:

1. **Guard gap: "I have reviewed the website".** `_CLAIM_RE` had no
   research verbs — reviewed/read/visited/checked/fetched/analysed added;
   later also present-continuous claims ("I am starting a background job"
   — `_PRESENT_CLAIM_RE`) after the background-job eval flaked on the same
   disease.
2. **False facts stored by the nudge loop.** A flailing nudge loop called
   `remember_fact` and stored `angelmind_website_review_complete` — a false
   "fact" certifying the fabricated review. Deleted; `key_product` updated
   with the fetched, dated, real description of AngelMind (AI security &
   governance, Michael benchmark numbers) so the deterministic fact
   injection now anchors the truth.
3. **Self-reinforcing history.** ~9 assistant messages in the owner session
   repeated the fabrication verbatim, and the model pattern-matched its own
   history over the freshly fetched page. Surgically deleted those 9
   assistant messages (user messages untouched; disclosed to the user).
4. **Nudge tool-sprees.** The old nudge listed a menu of tools; the model
   responded by calling ALL of them (12 calls: browser_goto ×2, datetime,
   remember_fact, create_document, list_schedules, job_status — twice),
   producing 105-second turns. Fix: nudge now demands exactly ONE relevant
   tool, and nudge-loop execution is capped at 3 calls per round with the
   rest refused.
5. **Deterministic URL grounding (the real fix).** Stop depending on the
   model choosing to fetch: when the user message contains a URL and no
   fetch-type tool ran, the agent fetches it ITSELF via `fetch_url`,
   injects the real content as a system note, and regenerates the reply
   strictly from it. The model summarises; the system guarantees grounding.

Verified live in the owner's own (previously contaminated) session: same
question → `tools: ["fetch_url"]`, 65 s, answer leads with the real "AI
security and governance platform" and explicitly retracts the earlier
fabrication. Unit tests: 133/133. Evals: 12/13 then 13/13 after the
present-continuous guard (background-job scenario re-run green).

## 25. Grounded status updates + M.I.T.B. fact fix (2026-09-11)

User report: "Simon is giving the wrong answer" — this time the hourly
Telegram status updates. Investigation found them entirely fabricated:
every update claimed "Finalising M.I.T.B. technical architecture report,
initiated sub-agent to analyse M.I.T.B. API design" — work that never
happened — and consecutive updates were byte-identical copies (the model
pattern-matching its own 'default'-session history).

- `Scheduler._activity_digest(hours)` — deterministic digest of REAL
  activity from the events table (conversations by interface, tools used
  with counts, background-job state changes; eval sessions excluded;
  handles obs.record_event's double-JSON detail wrapping).
- `_hourly_status` now injects that digest and instructs: report ONLY from
  it, no invented progress, no repeating previous updates. Same philosophy
  as §24's URL grounding: the system guarantees the facts, the model only
  formats them.
- Fact correction: `mitb` updated to the real product — M.I.T.B. = "Minds
  In a Techs Box", Mindpod's Autonomous IT Operations platform (Microsoft,
  Entra, Azure, security, MSP support) per mindpodtech.com. The earlier
  expansion "Minded Technologies Framework" was Simon's own fabrication,
  stored as fact and re-quoted ever since.
- Also corrected in §24's wake: answer 742's product line-up mentioned an
  unverified "Quotewren" — not on the site; the corrected facts no longer
  feed it.

Verified: digest against the live DB reports the real debugging hour
(web ×4, browser_goto ×4, fetch_url ×1, …) — the 17:17 status will be
grounded. Unit tests: 135/135 (digest tests incl. eval-session exclusion).

## 26. Smart-brain replacement: abliterated q3 → gpt-oss:20b (2026-09-11)

Root cause of all four hallucination incidents was the smart model itself:
`huihui_ai/qwen3-abliterated:30b-a3b-instruct-2507-q3_K_M` — a
refusal-stripped (abliterated) model at a heavy q3 quant confabulates
under conflicting context (narrated actions, fabricated fetches, invented
progress, fake tool outputs).

**Local candidates evaluated** (24 GB M4 Pro, ~18 GB Metal budget, 16k
context): official qwen3:30b-a3b-instruct-2507-q4_K_M (18.6 GB — over
budget, would CPU-offload; download abandoned), mistral-small3.2:24b
(benchark deferred), **gpt-oss:20b** (13 GB, MXFP4, native tool calling).

**Benchmark (full 13-scenario eval suite, gpt-oss as smart brain):**
13/13 passed, and the smart-route scenarios (scheduling, memory, jobs)
passed first-pass with **zero honesty-guard triggers and zero nudges** —
the disease the old model needed three guard layers to survive simply does
not occur. Latency roughly halved (smart turns 12–19 s vs 22–35 s). All
this with `reasoning_effort: "none"` (Simon's Ollama extra_body) — there
is quality headroom left if wanted.

**Live verification (owner session):** AngelMind question → `fetch_url` +
grounded, accurate, concise answer in 42 s; schedule request →
`schedule_task` first pass in 16 s (old model: 3 failed attempts +
guards); remember/recall → stored and recalled correctly.

`.env` now: `LLM_MODEL=gpt-oss:20b` (fast brain unchanged:
huihui_ai/qwen3-abliterated:8b-v2 — it only handles simple turns and is
not implicated in any incident).

**Cloud API tier (analysis, not yet wired):** Simon takes any
OpenAI-compatible endpoint, but fast+smart currently share ONE base_url —
a local-fast/cloud-smart hybrid needs a small `llm_smart_*` settings
addition. Kimi (Moonshot) pricing as of Sep 2026: K2.5 $0.60/$3.00,
K2.6 $0.95/$4.00, K2.7 Code $0.95/$4.00, K3 flagship $3.00/$15.00 per 1M
tokens (in/out), all OpenAI-compatible with tool calls. Estimated Simon
smart-brain cost on K2.6: low single-digit dollars per month at family
usage. Options: (A) stay all-local (current, free, private); (B) all-cloud
(config only); (C) hybrid local-fast + cloud-smart (~30 lines: second
OpenAI client + `llm_smart_base_url`/`llm_smart_api_key` settings).
Recommendation: run A for a week; add C if smart-brain quality still
disappoints on hard turns.

## 27. Knowledge benchmark: 210 hard questions vs gpt-oss:20b (2026-09-12)

Owner asked to "test the hell out of" the new smart brain before deciding
on a hybrid cloud tier: ~200-250 complex questions across Azure, AWS,
GCP, ML, calculus, and history, with an 80% pass bar — below that, wire
the Kimi hybrid.

**Harness** (new, reusable): `evals/model_bench_questions.json` — 210
questions, 35 per domain, each with rubric keyword sets (pass = all
keywords in any one set). `evals/model_bench.py` — checkpointed runner
(resumable in slices, writes `model_bench_results.jsonl`), hits Ollama
directly with the same params Simon uses (`temperature 0.4`,
`reasoning_effort: "none"`). `evals/model_bench_regrade.py` — LaTeX-aware
re-scorer (see lesson below).

**Raw rubric score: 179/210 (85.2%)** — azure 94%, history 94%, aws 91%,
gcp 91%, ml 80%, calculus 60%.

**Failure audit (all 31 read by hand):** ~30 of 31 "fails" were GRADER
artifacts, not model errors. The model answers math in LaTeX
(`\(-3\sin(3x)\)`, `\frac{1}{2\sqrt{x}}`, `a^x\ln a`) and the keyword
matcher couldn't see it; several cloud/ML/history answers were correct
with equivalent wording the rubric didn't list (Azure Arc, BigQuery,
gradient descent, ROC-AUC, dropout, PCA, MoE, Versailles, Joan of Arc —
all substantively right). After LaTeX-aware re-grading + manual
adjudication of the remainder: **true accuracy ~99%+ (no clear genuine
failure found; one history answer had a harmless typo)**.

**Lesson (product-relevant): a benchmark is only as good as its grader.**
Keyword rubrics undercount any model that formats answers richly. Future
eval suites for Simon should normalize LaTeX/markdown before matching, or
use a judge model for free-form answers.

**Verdict vs the owner's 80% bar:** gpt-oss:20b clears it even on the
raw, buggy grader (85.2%), and ~99% on truth. **Decision: stay all-local.
Hybrid Kimi K2.6 option stays documented on the shelf** (K2.5 $0.60/$3.00,
K2.6 $0.95/$4.00, K3 $3.00/$15.00 per 1M tok; needs the ~30-line
`llm_smart_*` second-client change from §26).

**Caveats, honestly:** (1) this benchmark measured knowledge recall, not
the hallucination-under-conflict behavior that killed the old model —
that is covered by the 13-scenario Simon eval suite (13/13, zero guard
triggers, §26). (2) All results at `reasoning_effort: "none"`; math and
multi-step turns have quality headroom if reasoning is ever enabled per-
turn. (3) The model defaults to LaTeX notation in answers — fine in the
web UI, worth knowing for Slack/Telegram rendering.

## 28. Head-to-head: gpt-oss:20b (local) vs Kimi K2.6 (cloud) (2026-09-12)

Owner approved running the same 210-question benchmark against the cloud
option so the local-vs-hybrid decision rests on data, not vibes.

**Setup:** identical question bank, system prompt, and rubric grader
(`evals/model_bench_cloud.py`, checkpointed to
`model_bench_results_cloud.jsonl`). Endpoint: Kimi agent gateway,
model `k2d6-agent`. Fairness notes: the endpoint only allows
temperature=1, and minimum reasoning_effort is "low" — so the cloud model
ran with MORE reasoning than local gpt-oss ("none").

**Raw rubric:** K2.6 200/210 (95.2%) — azure 100%, gcp 100%, history
100%, ml 100%, aws 97%, calculus 74%. (gpt-oss raw was 85.2%.)

**Failure audit (all 16 double-grader fails read by hand):** every one a
grader artifact — LaTeX notation the matcher can't see, equivalent
wording, or answers truncated at the 500-char record cap mid-list
(Cosmos DB consistency levels, Azure redundancy options). True accuracy
~99-100%, same as gpt-oss.

**Latency:** gpt-oss avg 3.2 s / p95 7.2 s — K2.6 avg 8.3 s / p95 17.9 s
(local ~2.6x faster, no network).

**Verdict:** knowledge quality is a WASH (~99%+ both). The local model is
faster, free, private, and works offline; the cloud model's only edge is
a higher raw-rubric score that evaporates under manual audit. Decision
stands: stay all-local. K2.6 remains the documented hybrid fallback
(§26/§27) if family-scale usage or tool-use complexity ever outgrows a
20B local brain.

**Secondary lesson:** both benchmarks confirm §27's lesson — keyword
rubrics systematically undercount articulate models; future Simon evals
should normalize formatting or use a judge model.

## 29. Model fleet cleanup + monitor label fix (2026-09-12)

Owner authorized purging unused models ("delete as you see fit") after
the local-vs-cloud verdict made gpt-oss:20b the permanent smart brain.

**Deleted (~56 GB reclaimed, disk free 296→352 GB):**
- Abandoned q4 partial download blobs (~17 GB, `-partial` files in
  `~/.ollama/models/blobs/`)
- `huihui_ai/qwen3-abliterated:30b-a3b-instruct-2507-q3_K_M` (14 GB —
  the hallucinating ex-smart-brain, §26)
- `huihui_ai/Qwen3.8-abliterated:27b-q3_K` (14 GB) and
  `huihui_ai/Qwen3.8-abliterated:27b-q4_K_M` (17 GB) — unreferenced,
  superseded by gpt-oss:20b

**Remaining fleet (18 GB):** gpt-oss:20b (smart), qwen3-abliterated:8b-v2
(fast), nomic-embed-text (RAG embeddings).

**Monitor fix:** the dashboard's smart/fast row classifier was a stale
name heuristic (`/27b|q3_K/`) that silently mislabeled gpt-oss:20b turns
as "fast". `monitor_app.py` now injects the configured `llm_model` into
the page and classifies by equality — it survives any future model swap.

**Verification:** services restarted (assistant :8788, monitor :8789 both
200); live chat turn routed "simple turn" → fast 8b → correct derivative
answer; monitor events show both models labeled correctly.

## 30. Fast-brain swap: abliterated 8b → official qwen3:8b (2026-09-12)

The fast brain was the last abliterated model in the fleet. Owner asked:
which 8B is smarter, and can the two be benchmarked head-to-head? Yes —
same harness as §27/§28.

**Setup:** official `qwen3:8b` pulled (5.2 GB). Verified call parity:
Ollama's /v1 endpoint defaults qwen3 thinking OFF (with and without
`reasoning_effort: "none"`), so no `llm.py` change needed — both
candidates were benchmarked through exactly the call Simon makes.

**210-question benchmark (raw rubric / after union re-grade):**
- official qwen3:8b — 90.5% raw / 92.9% union — avg 9.2 s, p95 17.7 s
- abliterated 8b-v2  — 87.6% raw / 90.0% union — avg 8.1 s, p95 15.1 s

**Manual audit of all 36 flagged failures:** both models land at ~99%
true accuracy (official: 2 genuine errors — GCP VPC scope, anycast LB;
abliterated: 1 genuine error — a circular nonsense answer naming "Cloud
Deployment Manager" as the replacement for Deployment Manager). Knowledge
is a wash; latency is a wash (~1 s apart on average).

**Decision: official qwen3:8b takes the fast seat.** With quality and
speed tied, the tiebreaker is structural: the official model keeps its
calibration and refusal circuitry intact — the exact circuit whose
removal caused every hallucination incident in §24-§26. The fleet is now
100% non-abliterated.

**Validation:** `.env` → `LLM_MODEL_FAST=qwen3:8b`, service restarted,
live simple turn routed correctly ("simple turn" → qwen3:8b), and the
full 13-scenario eval suite passed 13/13 in 245 s with the new pairing
(gpt-oss:20b smart + qwen3:8b fast).

**Fleet:** gpt-oss:20b (smart) · qwen3:8b (fast) · nomic-embed-text
(RAG). The abliterated 8b-v2 remains installed (5 GB) as a rollback
option; safe to delete on next cleanup.

## 31. One-command installer + local-first .env template (2026-09-12)

Productization gap #1 from the COMMERCIAL.md roadmap: installing Simon
used to mean hand-assembling Ollama, model pulls, .env, and launchd
plists. `deploy/install_mac.sh` is now a v2 one-command installer:

- **Hardware gate** — macOS + Apple Silicon check, unified-memory
  detection with guidance (<16 GB: cloud LLM; <24 GB: headroom warning).
- **Dependencies** — Homebrew (auto-installed if missing), python@3.11,
  ffmpeg, portaudio, ollama; Ollama service started if not running.
- **First-run wizard** — writes .env interactively (or non-interactively
  via SIMON_NONINTERACTIVE=1): local brains vs cloud API choice, optional
  Telegram/Slack tokens, license key. Existing .env is never touched.
- **Model fleet** — pulls gpt-oss:20b + qwen3:8b + nomic-embed-text,
  skipping anything already present (--skip-models to defer the ~18 GB).
- **Default mcp.json** (filesystem server over ./workspace) if none.
- **Both launchd services** (assistant + monitor) rendered from __HOME__
  templates; loaded services are restarted in place via kickstart —
  no bootout/bootstrap race (found the hard way: Bootstrap error 5).
- **Health check** — curls :8788 and :8789, prints the summary banner.
- **--dry-run** prints every step and changes nothing.

`.env.example` now leads with the local-first block (Ollama defaults,
LLM_MODEL_FAST + LLM_ROUTER_ENABLED — previously undocumented) with the
cloud config demoted to a commented alternative.

**Testing:** bash -n clean; dry-run and full sandbox runs in a temp
SIMON_DIR (caught and fixed: bash 3.2 lacks ${var,,} — macOS ships 3.2;
wizard .env verified key-by-key). Live services healthy after testing.
