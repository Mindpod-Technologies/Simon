# Simon — Feature Log & Review Guide

Everything built so far, grouped by theme, with **where to see it live** and
**how it's verified** (tests / evals / live runs). 451 unit tests and 17 eval
scenarios guard the whole list; every item links to its proof.

Dashboards (sign in with your Simon password):

| Surface | URL | What it shows |
|---|---|---|
| **Monitoring portal** | http://localhost:8789/ | Live ops feed: every turn, model route, latency, tool calls, errors, eval scores |
| **Simon app** | http://localhost:8788/ | Chat + WORKSPACE panel: artifacts, uploads, background jobs, automations, plugins |
| **Marketing site** | http://localhost:8790/ | Pricing, downloads (mac DMG + Windows x64/arm64), feature story |
| **Customer portal** | http://localhost:8793/ | (Simon Cloud host) per-business usage dashboard + support cases |
| **Settings** | http://localhost:8788/settings | Models, license, memory facts, **Standing approvals**, updates |

---

## 1. Trust & safety — "autonomous, not unsupervised"

- **Approval gate** — `send_email`, destructive shell, smart-home calls,
  `delegate_dev`, MCP mutations (push/merge/delete/pay…) park and ask
  *approve / approve always / reject* before executing. Rescue paths can't
  bypass it. `f7c3081`
- **Cross-channel approval** — reply `approve` from Telegram to settle
  something asked on the web; `approve 3` when several are pending. `585fc8d`
- **Standing approvals** — "approve always" grants the exact operation
  (per-recipient, per-command); inspect/revoke in Settings → Standing
  approvals. `74e13eb`
- **Honesty architecture** — deterministic intercepts: unknown personal
  facts, claimed-action guards, false-refusal rescue, cut-off detection.
  Evals: `honest-about-unknown-personal-fact`, `sensitive-action-asks-approval`.
- **Security hygiene** — SECURITY.md disclosure policy, pre-commit secret
  scanner, verified zero secrets in git history. `1a472d5`

## 2. Brain & models

- **Three-tier routing** — qwen3:8b (fast) / gpt-oss:20b (smart) / frontier
  cloud (K3; Grok = 3 env lines). Auto-escalation on tool errors, frontier
  rescue on local failure.
- **Jev decision layer** — TypeSafe "System One" model makes calibrated
  routing calls + eval judging; soft-fails to the keyword router when
  unset/unreachable. `455730a`
- **Capability tour** — "what can you do" (23 phrasings sweep-verified)
  answered instantly from the live tool registry, never hallucinated. `7724e12`, `18ab80e`
- **Research-intent routing** — "web search / look it up / google it" always
  gets tools, never the tool-less fast tier. `964422c`
- **Latency** — `keep_alive: 24h` + model warm-up at boot. `e871026`

## 3. Channels & family

- **Every surface** — Web, Slack, Teams, Telegram, voice CLI, email (own
  M365 mailbox, 5-min watch).
- **Per-person profiles** — family members auto-named from Telegram;
  'sir' is the owner's alone; facts attributed per person. `b43aaeb`
- **Per-person notifications** — her schedules/jobs/reminders land in her
  Telegram; your operational stream (mail, jobs, approvals) is owner-only
  (the "bleed" fix). `456d62e`, `964422c`
- **Telegram remote control** — `/status` (jobs + automations + pending
  approvals), job pickup/completion pushes, approval asks on your phone. `c906541`
- **Voice** — markdown stripped before TTS on every channel (no more
  "asterisk asterisk"); speech queue so replies never talk over each other. `a8f29d1`

## 4. Simon Work (desktop)

- **Electron shell** + one-click first-run (installs Ollama for you),
  license gate, auto-update. Windows NSIS x64 + arm64 installers on the
  release. `1759d87`
- **Assistant-grade UI** — glowing orb hero (breathes/thinks/speaks),
  suggestion chips, markdown-rich bubbles, task workspaces with an
  Electron-safe modal, no-cache shell so UI updates land live. `734e4a8`
- **Menu-bar tray** — ● while jobs run, ❗ + macOS notification when
  approval is needed, Quick Ask popup (⌥Space). `af4bc8d`
- **Input queue** — type while Simon works; messages queue and drain in
  order. `9f695b9`

## 5. Browser, tools & documents

- **Playwright browser, visible** — headed Chrome you can watch; persistent
  profile (logins survive); **takeover mode** attaches to YOUR Chrome via
  CDP (scripts/chrome_debug.sh). `9f695b9`, `74e13eb`
- **Browser eyes** — goto returns a form-field map; fills are read back and
  verified; forgiving selectors; persona forbids unverified claims and
  irreversible submits. `51126d5`
- **Sandbox exec** — Python in a throwaway Docker container (no network,
  512 MB, read-only, workspace-only mount). Auto-activates when Docker is
  installed. `74e13eb`
- **Email with real attachments** — Graph fileAttachment, workspace-confined,
  5 MB cap, approval-gated. `5924c18`
- **Documents & charts** — create_document / create_chart → Artifacts panel;
  uploads ingest into RAG.

## 6. RAG 2.0 & memory

- **Scoped knowledge spaces** — every chunk namespaced (you / family /
  shared); nobody retrieves across spaces. Privacy hole closed. `2ee4ba0`
- **Hybrid retrieval** — local Ollama embeddings + SQLite FTS5 keyword,
  reciprocal-rank fusion. Exact strings AND fuzzy semantics.
- **Cited answers** — `[from report.md §2]` inline citations required by
  persona. Eval scenario `rag-recall-is-grounded-and-cited` guards it weekly.
- **Long-term facts** — remembered across every channel, editable in
  Settings → Memory.

## 7. Business: billing, Simon Cloud, portal

- **Stripe pipeline** — checkout → webhook (HMAC-verified) → signed license
  key minted + emailed + success page. Idempotent. Service on :8792. `5a8752a`
- **Simon Cloud** — single-tenant-per-customer hosting: one isolated
  container + auto-HTTPS subdomain per customer; provisioning wired to
  Stripe fulfillment; one-paste VPS bootstrap (Docker + Caddy + DNS
  pre-flight + billing + portal systemd). `3097674`, `2d63357`
- **Customer portal** — sign in with license key; hosted customers see
  7-day usage (conversations, channels, latency, automations, jobs,
  approvals); self-host sees license status (never phone home); support
  cases with SC-XXXXX refs and status lifecycle. `0ef48bc`

## 8. QA & observability

- **451 unit tests** across every module (run: `pytest tests/`).
- **17 eval scenarios** run weekly by the scheduler (results pushed to
  Telegram); live-driving sweep script at `scripts/viktor_sweep.js`.
- **Observability events** for every turn (route, model, latency, tools,
  errors) powering :8789.
- **QA checklist** — `docs/QA_CHECKLIST.md`: the acceptance pass for every
  release, including GUI-driving steps via computer-use.

---

*Generated 2026-09-21. 77 commits on main. See also: `docs/FEATURE_PARITY.md`
(Simon vs Viktor / Claude Cowork / Kimi Work).*
