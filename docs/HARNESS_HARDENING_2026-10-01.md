# Harness Hardening for Production Scale — 2026-10-01

Owner directive: strengthen Simon's harness across five pillars for
rapid customer scaling. Method: 4-agent research swarm (2026 best
practices), gap analysis vs Simon, implementation. Research reports are in
this commit's workspace; this doc is the what-shipped record.

## 1. System Prompt

**Research:** stable-front/volatile-back layering (a volatile value in the
prefix silently kills provider caching — the #1 production prompt bug);
persona/contract separation (canonical); prompts-as-code with CI gates.

**Shipped:**
- Layered assembly: SOUL.md (character, tenant-editable) → operational
  contract (immutable, in code) → LIVE CONTEXT tail (date/mailbox/profile/
  facts/RAG/assignment — newest last). The stable prefix is now
  byte-identical across turns AND days (date moved out of it).
- Cache breakpoints land at the stable/volatile boundary by construction.

**Next (not yet):** Promptfoo CI gate, per-tenant SOUL tri-state
(inherit/default/custom) with sanitization — the schema supports it, the
multi-tenant loader comes with the first cloud customer.

## 2. Tools

**Research:** silent arg-dropping is a documented worst-practice anti-pattern;
idempotency follows the Stripe/IETF pattern; batch parking must not drop
siblings.

**Shipped:**
- **Strict-arg contract**: mutating tools REFUSE unknown args with
  self-correction guidance (read-only tools keep the forgiving drop).
  Central classification: explicit flag + builtin set + mutation-verb
  pattern (covers MCP tools automatically).
- **Batch-safe approvals**: a parked sensitive call no longer drops the rest
  of the batch — siblings execute, the parked call gets a proper
  tool-result placeholder (protocol stays whole).
- **Idempotency**: derived keys (sha256 of tool+canonical args) with a
  10-minute TTL; identical repeats return the recorded result. No more
  double-sent emails from gate retries. Errors never cache; tools with
  their own clobber protection (teach_skill) opt out.

## 3. Feedback Loops & Verification

**Research:** online evals on production traces, drift canaries catch silent
provider model rolls, incident→test-case discipline.

**Shipped:**
- **Drift canary** in the 2-hourly maintenance battery: fixed one-word
  factual probe ("capital of France") through the fast tier — if the answer
  drifts, the model under us changed.
- The existing substrate: 21-scenario weekly evals, 2-hourly maintenance
  battery, per-turn spans now feeding them.

**Next:** LLM-as-judge calibrated against human labels; eval-gated deploys.

## 4. Guardrails & Permissions

**Research:** deterministic policy evaluated OUTSIDE the model
(Cedar/OPA/Cerbos lineage), verdicts allow/deny/approve, base pack
immutable per deployment, tenant overrides layered.

**Shipped — `policy.yml` + `simon/policy.py`:**
- Guardrails as data: glob-matched tool rules with `when:` arg-regex
  clauses and arg-templated reasons ("send an email to {to}").
- Verdicts: approve (park), deny (blocked outright — new: policy denials
  never execute, never park, at ALL THREE execution sites), allow.
- approvals.assess consults the pack first; code lists are the fallback.
- **Tenant-ready**: SIMON_POLICY_FILE per deployment; per-customer packs
  layer on later.

## 5. Observability & Memory

**Research:** OTel GenAI conventions (invoke_agent → chat/tool spans),
token/cost metering is self-owned (the conventions carry NO tenancy
attributes), memory poisoning defenses are architectural (provenance caps),
GDPR needs tag-at-write.

**Shipped:**
- **Trace spans** (`spans` table): one trace per turn; chat spans carry
  model + prompt/completion tokens + duration; tool spans carry duration.
  Live-verified on real turns.
- **usage_by_day()** rollup — the cost-accounting substrate for per-customer
  metering.
- Memory: provenance + quarantine shipped 2026-09-28; temporal validity
  fields are the next increment.

## Test state

600 → this hardening round: **600+ passing** including new suites for
strict-args, idempotency, policy packs, spans, and the drift canary.
