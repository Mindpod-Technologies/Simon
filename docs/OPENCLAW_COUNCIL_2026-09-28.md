# Council Review: Rebuild Simon on OpenClaw? — 2026-09-28

**Question from the owner:** Simon still shows memory/tool-call/retrieval
issues — would rebuilding on OpenClaw (or adopting major components) fix the
architecture?

**Council:** two evidence-mining passes (OpenClaw repo architecture,
Simon production-failure audit) + chair synthesis. Full OpenClaw brief and
Simon audit are in this commit's conversation; key citations inline.

## Verdict: NO rebuild. Subtract, don't substitute.

The bench already answered the empirical question: same Mac, same frontier
model — **Simon 20/20, OpenClaw 16/20**, with all four OpenClaw failures in
the fabrication/approval classes Simon's deterministic layer prevents
(docs/BENCH_OPENCLAW_2026-09-27.md). The repo mining confirms it is
structural, not incidental: **OpenClaw has no mechanism that verifies a
reply's claims against actual tool executions** (receipts exist but nothing
cross-checks the reply before delivery). Simon's gate is that check.

## Where Simon's failures actually come from (the audit)

| Class | Share | Status |
|---|---|---|
| Abliterated local models at heavy quants | ~70% of all incidents | **Retired** — fleet is 100% non-abliterated since 2026-09-12 (CHANGES §26/§30); gpt-oss:20b passed 13/13 with zero guard triggers |
| Harness bugs | ~20% | Fixed with regression tests (B1–B18) |
| Infrastructure (24GB Metal ceiling, crash loops) | ~5% | Mitigated (keep_alive 2h, Laya off); hardware-bound |
| Genuinely open design items | ~5% | Small local diffs — see below |

The owner's "memory/tool/retrieval issues" are, on the record, mostly
**history**: poisoned facts and confabulations from the abliterated era.
Retrieval itself is already what OpenClaw would offer (FTS5 BM25 + vector +
RRF hybrid shipped 2026-09-21). A better retriever would just retrieve
poison more reliably — the fix is write-side provenance, not a new stack.

## What OpenClaw is genuinely better at

- Mature channel coverage (WhatsApp, iMessage, 20+ native extensions) —
  plugin-bound, not extractable as standalone packages
- `packages/tool-call-repair` (5.7k LOC stream normalizer) — the ONE cleanly
  extractable component; its *ideas* (did-you-mean recovery, protected
  ranges) are already mirrored in Simon's sanitizer
- Lane Queue with capacity groups and timeout taxonomy
- Skills marketplace (ClawHub) with integrity/lockfile tooling

## What Simon has that OpenClaw lacks (porting cost = rebuild)

Receipts enforcement + completion gate (7 integration sites), approval gate
plumbed through every interface, durable assignments, eval suite that just
caught 4 OpenClaw failures, structured observability, and the entire
commercial layer (licensing, Stripe, portal, cloud provisioning). OpenClaw
also carries CVE-2026-25253 (one-click RCE) and ambient host authority —
unacceptable for family/customer exposure without a security audit.

## Action items (subtraction plan — the principled version of the pyramid)

1. **Receipts everywhere** — extend the gate's receipt ledger to ALL turns;
   render delivery claims from receipts, then DELETE `_looks_dishonest` and
   the prose-regex guard families per ADR-3's criterion (4 clean weekly
   evals). The pyramid is insurance against models we no longer run.
2. **Constrained generation** — Ollama JSON-schema decoding for tool-intent
   turns: structurally incapable of prose-only answers; replaces pseudo-call
   rescue + claimed-action nudge + dodge detector in one move.
3. **Fact-write provenance/quarantine** — stop memory poisoning at write
   time (two production incidents came from this).
4. **Planner reality check** — Laya is off (Metal crashes), so the typed
   planner never fires in production; the ≥2-verb fallback is doing the work.
   Either move planning to the frontier tier or admit the fallback IS the
   planner and simplify.
5. **Tool-call safety** — arg-drop must never silently change a mutating
   call's semantics (namespace/recipient/dry_run); approval parking must not
   silently drop the rest of a tool-call batch.
6. **Channels** — when WhatsApp/iMessage becomes a requirement, evaluate
   OpenClaw AS A CHANNEL GATEWAY fronting Simon (its extensions are
   plugin-bound; reuse means running its gateway, not importing code).

## ADR-4 trigger review

Criteria NOT met: evals at 20/20 (not <15/20 twice), failures this week were
model/infra-class with harness fixes shipped same-day, and no required
capability is cheaper to inherit yet. Revisit when any two trip.
