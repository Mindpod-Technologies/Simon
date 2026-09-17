# Simon / Simon Work — Feature Parity Map

How Simon stacks up against the products we benchmark against, and what we
build next. Status legend: **✅ shipped** · **🟡 partial** · **⬜ not yet**.

Competitor notes on Claude Cowork reflect Anthropic's public docs and press as
of Sept 2026 (GA since March 2026; cloud-run tasks in beta; mobile as remote
control). Kimi Work notes reflect its shipped local-agent runtime. "Viktor"
notes are directional — it is a moving target, so we benchmark the *category*
(persistent autonomous chief-of-staff) rather than a feature checklist.

## 1. Core agent

| Capability | Claude Cowork | Kimi Work | Simon |
|---|---|---|---|
| Multi-step autonomous tasks (one prompt → many steps) | ✅ | ✅ | ✅ (`agent.py` tool loop, background jobs) |
| Local file read/write/organize | ✅ | ✅ | ✅ (filesystem MCP + uploads + artifacts) |
| Document creation (docx/xlsx/pptx/pdf/charts) | ✅ | ✅ | ✅ (`docs.py`, `charts.py`, `artifacts.py`) |
| Persistent memory across sessions | ✅ | ✅ | ✅ (`memory.py` facts + conversations + RAG) |
| Cross-channel identity (same person on web/Slack/Telegram/email) | 🟡 (account-based) | 🟡 | ✅ (identity map across all four channels) |
| Web browsing / computer use | ✅ | ✅ | ✅ (`browser.py`, `computer.py`, `vision.py`) |
| Voice in/out | 🟡 | 🟡 | ✅ (`voice/`, voice CLI, TTS to Slack) |
| Sub-agents / delegated dev work | ✅ (Agent Teams) | ✅ | ✅ (`subagents.py`, `dev_delegate.py` → Claude/Codex) |
| Proactive behavior (morning briefing, reminders, mail triage) | ✅ | ✅ | ✅ (`scheduler.py`: briefing, reminders, 5-min mail check) |
| Approval gate — asks before anything irreversible ("autonomous, not unsupervised") | ✅ (its signature feature) | ✅ | ✅ (`approvals.py`: send_email, destructive shell, smart-home, MCP mutations park until owner replies approve/reject; cross-channel — approve from Telegram what was asked on the web; pushed to phone; survives restarts) |
| Pushes back instead of blindly obeying | ✅ | 🟡 | 🟡 (persona-level; approval gate catches the irreversible cases deterministically) |
| Model routing (fast/smart/frontier) | ⬜ (fixed models) | ⬜ | ✅ (qwen3:8b fast / gpt-oss:20b smart / K3 frontier) |
| Runs fully local / offline-capable | ⬜ (cloud) | 🟡 (local runtime, cloud model) | ✅ (Ollama; cloud models optional) |

## 2. Platform & extensibility

| Capability | Claude Cowork | Kimi Work | Simon Work |
|---|---|---|---|
| Desktop app (macOS) | ✅ | ✅ | ✅ (Electron shell, one-click Ollama install) |
| Desktop app (Windows) | ✅ | ✅ | 🟡 (NSIS target exists; needs 1.5.1 rebuild + test) |
| Mobile access / remote control | ✅ (phone steers desktop) | 🟡 | ✅ (Telegram/Slack act as mobile surfaces) |
| MCP connectors | ✅ (MCP-native, 100s of servers) | ✅ | ✅ (`mcp_client.py`, `mcp.json`, GitHub + filesystem wired) |
| Drop-in plugins | ✅ (plugin bundles) | ✅ | ✅ (`plugins/` auto-load, shown in WORKSPACE panel) |
| Skills (reusable procedures) | ✅ (Claude Skills) | ✅ | ✅ (`skills.py` + shipping productivity skills) |
| Scheduled / recurring automations created in chat | ✅ | ✅ | ✅ (`schedule_task` tool, AUTOMATIONS panel) |
| App marketplace / directory | ✅ | ✅ | ⬜ |
| SSO / RBAC / audit log (enterprise) | ✅ (Enterprise tier) | 🟡 | 🟡 (auth gate + audit log; no SSO/RBAC yet) |

## 3. Trust & operations (our differentiators)

| Capability | Claude Cowork | Kimi Work | Simon |
|---|---|---|---|
| Built-in eval suite with scored scenarios | ⬜ (internal only) | ⬜ | ✅ (`evals/` — 14 scenarios, weekly auto-run) |
| Observability dashboard (latency, routes, errors) | 🟡 (admin analytics) | 🟡 | ✅ (`obs.py`, monitor app on :8791) |
| Honesty guards (no fabricated answers) | 🟡 | 🟡 | ✅ (dedicated intercept + eval scenarios) |
| User owns all data (local SQLite, no cloud) | ⬜ | 🟡 | ✅ |
| License/tier system for commercial sale | n/a (subscription) | n/a | ✅ (`licensing.py`, signed keys, Free/Pro) |

## 4. Product split (confirmed)

One brain, two surfaces, one brand family:

- **Simon** — the agent itself, reachable everywhere: web, Slack, Teams,
  Telegram, email, voice. The Viktor analog — the AI employee.
- **Simon Work** — Simon on your desktop, plus bells and whistles: Electron
  shell over the same UI, one-click installer (bundled Ollama), licensing,
  auto-updates, WORKSPACE panel, and the marketing site. The Claude Cowork /
  Kimi Work analog. It is NOT a second agent — it is the same Simon with
  local-first muscle (offline models, deep file access, OS-level hands).

Marketing line: "Simon is your AI employee. Simon Work is him sitting at
your desk."

## 5. Build-next list (priority order)

1. **Windows installer at 1.5.1** — rebuild NSIS, test on a real Windows box.
2. **Stripe checkout** — replace `mailto:sales@` Buy Pro flow with payment
   links + webhook that issues license keys automatically.
3. **Mobile-steering polish** — Telegram is our "phone app"; add task
   progress push (`/status`, job notifications) so it matches Cowork's
   mobile remote-control story.
4. **Plugin/marketplace skeleton** — versioned plugin manifest + `simon
   plugin install <url>` so third parties can ship connectors.
5. **Cloud-run option** — Cowork's "close your laptop, work continues" is
   its killer feature; our answer is the user's VPS: package
   `docker compose up` Simon for always-on deployment.
6. **RBAC-lite** — per-user roles on shared deployments (family/team),
   building on the existing auth gate.
7. **In-app eval/obs surfaces** — expose eval scores and the observability
   feed inside Simon Work settings instead of a separate portal.
