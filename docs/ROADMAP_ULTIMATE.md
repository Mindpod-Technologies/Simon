# The Ultimate Simon — product roadmap
*Synthesized 2026-09-29 from a three-agent research swarm (competitor intelligence, isolated-compute options, tool/integration landscape). Sources in the agent reports; category reprices monthly.*

## Where the market actually is (September 2026)

"Can it act?" is over — memory, chat-native presence, own-computer execution, approvals, scheduled work, OAuth integrations, and pro deliverables are **table stakes** (Viktor, Grok Bot, Claude Cowork, Kimi Work, Manus, Devin all have them).

The new battleground: **"can you trust it unsupervised?"** Winners are decided by proactive initiative, always-on execution, teachability, and verifiability. Pricing model is itself a weapon — credit-burn is the #1 churn driver (Viktor, Manus, Perplexity), and flat-rate challengers are growing on that weakness.

## Simon's position vs the table

Already shipped (table stakes ✓): chat-native multi-channel, memory with provenance, scheduled jobs, approvals, documents, browser control, computer-use, coding-agent delegation, RAG, evals, observability, billing/portal.

| Battleground capability | Simon today | Best-in-class | Gap |
|---|---|---|---|
| **Verifiable outcomes / audit trail** | ✅ receipts, gates, evals, activity terminal | Devin replay, Viktor traceable outputs | **We're the moat — extend it** |
| Proactive initiative (volunteers work) | weekly suggestions | Viktor's discovery agent (2×/week, per-person) | Upgrade ours to per-member, metric-watching |
| Always-on execution (device off) | ❌ Mac sleeps = dead | Grok Bot cloud VMs, Cowork server-side | Simon Cloud on the VPS |
| Teach-by-demonstration | teach_skill (text steps) | Grok teach-a-task (screen-record → skill) | Add screen-record capture |
| Multi-agent parallel work | subagents (sequential-ish) | Kimi Swarm (300), Manus Wide Research | Parallel fan-out on the VPS |
| Watchable VM desktop | ❌ | nobody mainstream does watchable-well | **Kasm/Webtop — see below** |
| Integration breadth | ~10 (MCP) | Viktor 3,200+ | MCP gateway (Nango) — own the auth layer |
| Flat/self-hosted pricing | ✅ our model | credit-burn everywhere | **Weaponize it** |

## The "nasty" feature: Simon's own watchable desktop

Nobody mainstream lets you WATCH your agent's desktop live. Grok Bot has the VM but not the window. We can beat it:

- **Tier 1 (local, $0):** Kasm/Webtop container — Simon gets a full Linux desktop in Docker, the owner opens `https://localhost:6901` and **watches him work live in a browser**. Plus Docker Sandboxes (`sbx` microVM) for untrusted code execution. (Requires Docker Desktop on the Mac — currently not installed; the sandbox tool is already wired to light up when it is.)
- **Tier 2 (cloud, on the VPS):** Fly.io Sprites (~$0.44 per 4h session, persistent, MCP-native) or Scrapybara ($29/100h) for a hosted watchable desktop — pairs with Simon Cloud.
- **Tart + VNC** only when a real macOS GUI is required (limit: 2 macOS VMs).

Integration: new `simon_vm` tool family (exec, screenshot, open URL, type/click) with the session stream linked in chat — "watch me live" becomes a link Simon drops in Telegram.

## The next-90-days slate (ranked, impact ÷ effort)

1. **Always-on Simon Cloud on the VPS** — the #1 structural gap; Simon runs when the Mac sleeps. (Simon Cloud exists; deploy it.)
2. **Watchable VM desktop (Kasm/Webtop, local)** — the demo feature nobody else has. "Watch Simon work" is trust made visible.
3. **Viktor-style proactive discovery agent** — upgrade weekly suggestions to per-member, pattern-watching proposals 2×/week.
4. **WhatsApp + iMessage channels** — where family actually lives (Baileys/iMessage-on-Mac paths; accept the ToS notes).
5. **Voice phone calls (Vapi/Retell)** — Simon calls the dentist. Nothing demos better.
6. **Teach-a-task screen recording** — record once in the desktop app → compiled skill (we already have teach_skill; add capture).
7. **"What changed" page watcher** — daily diffs of watched pages; habit-forming proactivity.
8. **Google Workspace + Notion + Linear MCP trio** — the biggest missing productivity surface.
9. **Subscription audit (Plaid read-only)** — the most viscerally valuable money feature.
10. **Self-improvement loop** — repeated workflow twice → Simon proposes a skill, owner approves once.

**Pricing posture:** lean into flat + self-hosted as the anti-credit-burn positioning ("Simon never meters you") — with the commercial layer we already built.

## What NOT to build (right now)

- Full deck designer (document export covers it), official WhatsApp Business API (Meta bans general assistants), bespoke RCS, and anything requiring we give up the receipts/approval moat for speed.

## Sources

Competitor intel: viktor.com, Lindy's Viktor review, Layer3 Grok Bot guide, CloudZero Grok pricing, fast.io Cowork review, usecarly Kimi Work, Taskade Manus review, TechJack OpenClaw. Compute: e2b, Daytona, Modal, Fly.io Sprites, Scrapybara, Docker Sandboxes, Tart (jonnyzzz/tart-skills), Kasm/Webtop, Bytebot. Full URLs in the swarm reports.
