# COMMERCIAL.md — Selling Simon as a Product

A practical playbook for turning this repository into a sellable,
self-hosted "AI worker" product, in the spirit of products like Viktor.

---

## 1. Product positioning

**One-liner:** *Simon is a self-hosted AI employee — a JARVIS-style assistant
that answers on Telegram, voice, and web, remembers your business, and takes
real actions with tools — without sending your data to anyone's cloud.*

Angles that sell against SaaS competitors (ChatGPT Team, Copilot, hosted
agent platforms):

- **Privacy-first / data sovereignty.** All memory is a local SQLite file.
  Voice transcription runs locally (faster-whisper). The only outbound calls
  are to the LLM endpoint the customer *chooses* (including fully local
  Ollama — air-gap friendly).
- **No per-token SaaS markup.** Customer brings their own LLM key (or local
  model). You sell software, not metered tokens.
- **Ownership.** Source-available, self-hosted, Docker or bare metal. No
  subscription lock-in fear; no vendor shutdown risk.
- **Offline license keys, no phone-home** (see §4) — itself a selling point
  for privacy-conscious buyers.

Target buyers: solo founders, agencies, small IT shops, homelab/prosumer
users who already pay for tools like Tailscale, Synology, Home Assistant.

## 2. Packaging tiers

Licensing is plan-based (`trial` / `pro` / `business`) via signed keys
(`SIMON_LICENSE_KEY`, enforced when `SIMON_REQUIRE_LICENSE=true`). The key
*certifies* the plan; suggested gating is by distribution channel and
support level rather than crippling code — keep enforcement honor-system
(see §4).

| Tier | Suggested price | What the customer gets |
|------|-----------------|------------------------|
| **trial** | free | Full source, personal/evaluation use, community support via issues. Default posture: no key required. |
| **pro** | ~$149 one-time (or $12/mo) per seat | Commercial-use license for one operator, private docker image / release downloads, 12 months of updates, email support. |
| **business** | ~$499 one-time (or $49/mo) per seat, volume discounts | Up to N seats, white-label rights (§5), priority support, onboarding call, influence on roadmap, optionally an invoice/PO flow. |

Tips: lifetime-deal (LTD) launches convert well in self-hosted communities;
annual "updates & support" renewals create recurring revenue without
rent-seeking on usage.

## 3. Fulfillment flow

Keys are minted with the vendor-only tool:

```bash
python tools/keygen.py --plan pro --email customer@x.com --exp 2027-01-01
# -> SIMON-pro-eyJlbWFpbCI6...<sig>
```

Recommended pipeline, cheapest first:

1. **Sell** via a Stripe Payment Link (one product per tier) or Lemon Squeezy /
   Gumroad if you prefer merchant-of-record VAT handling.
2. **Deliver** the key automatically:
   - *Zapier/n8n/Make:* trigger on Stripe `checkout.session.completed` →
     call a tiny webhook you host that runs `keygen.py` (or re-implement the
     20-line signing logic in the workflow) → email the key with install
     instructions. Keep the vendor-only keygen and its private key on that server (never in this repo)
     only.
   - *Manual:* fine at low volume — run keygen, paste into an email template.
3. **Activate:** customer sets `SIMON_LICENSE_KEY` and
   `SIMON_REQUIRE_LICENSE=true` in `.env`. `run.py` validates at startup and
   prints a polite message on failure.
4. **Access:** grant the customer the private repo, release tarballs, or the
   private docker tag (see §6).

## 4. License enforcement philosophy

- **Honor-system, offline, no phone-home.** Keys are Ed25519-signed tokens
  verified against a public key embedded in `simon/licensing.py`. There is
  no activation server, no telemetry, no kill switch. Say this on the landing
  page — it converts the exact audience that buys self-hosted software.
- **Never break OSS/personal users.** With no key and
  `SIMON_REQUIRE_LICENSE=false` (default), Simon runs as a full `trial`.
  The licensing module even degrades gracefully if `cryptography` isn't
  installed.
- **Piracy posture:** keys are name-stamped (customer email embedded and
  shown in logs). Don't arms-race crackers; price fairly and make buying
  easier than stealing. Rotate the keypair (`--rotate`) only on a real leak,
  and reissue keys to legitimate customers.
- **Expiry (`exp`)** is for subscriptions/trials; leave empty for LTDs.

## 5. White-label notes (business tier)

Customers may rebrand their deployment:

- **Persona:** edit `simon/persona.py` (`SIMON_SYSTEM_PROMPT` — name, accent
  of the prose, formality). TTS voice is already configurable (`TTS_VOICE`).
- **Name/env vars:** env keys are the `SIMON_*` prefix and
  `simon_workspace_dir`/`simon_allow_shell`; a rename is a mechanical
  find-replace across `simon/config.py`, `.env.example`, and docs. The
  license-key prefix `SIMON-` lives in `simon/licensing.py` and the vendor-only keygen.
- **Web UI:** `web/static/index.html` is dependency-free — swap title,
  colors, logo freely.
- Recommend business-tier customers keep the `LICENSE` file intact and
  attribution in source headers.

## 6. Support & updates model

- **Releases:** tag semver releases in git (`v1.1.0`); attach release notes.
  Pro/business customers pull tags from a private repo mirror or download
  tarballs.
- **Docker:** publish tags like `vendor/simon:1.1.0` and `vendor/simon:latest`
  to a private registry; customers `docker compose pull && up -d`.
- **Update window:** 12 months of updates included, then a discounted
  renewal. Security fixes go to all supported plans.
- **Support channels:** email for pro, dedicated channel + SLA for business.
  Keep a public issue tracker for the trial tier — it doubles as marketing.

## 7. Legal basics (not legal advice)

- **Third-party API terms flow through to the customer.** They bring their
  own OpenAI/Anthropic/Telegram/Google keys and must accept those providers'
  terms. Say so explicitly in your terms of sale.
- **Trademark:** do NOT market using "Viktor", "JARVIS", "Iron Man", or
  other Marvel/third-party marks. "JARVIS-style" comparisons in docs are
  risky enough; keep marketing to your own brand ("Simon"). Don't use a
  competitor's name in ads or SEO pages.
- **License:** ship the `LICENSE` file (source-available: personal free,
  commercial requires a purchased key, private keygen never redistributed).
- **Taxes:** Stripe Payment Links don't remit VAT/sales tax for you — use a
  merchant-of-record (Lemon Squeezy/Paddle) or register as required.
- **Privacy story:** since Simon processes personal data by design, a short
  "no telemetry" statement is both true and a differentiator.

## 8. Landing-page outline

1. **Hero:** "Your self-hosted AI employee." + 60s demo video (Telegram
   voice note → Simon answers, checks calendar, sends email).
2. **Privacy banner:** "Your data never leaves your server. No telemetry.
   Offline license keys. Works with local models."
3. **What he does:** 4–6 cards (briefings, reminders, email triage, smart
   home, web research, notes/memory) with screenshots of the web UI.
4. **How it works:** architecture diagram + "bring your own LLM" (OpenAI,
   Anthropic, Ollama, OpenRouter).
5. **Pricing:** the three tiers from §2, honest feature comparison.
6. **Self-hosting section:** Docker one-liner, Mac Mini / VPS guides,
   Tailscale remote access.
7. **FAQ:** license terms, white-label, refund policy, "what if you
   disappear?" (answer: source-available + offline keys = they keep running).
8. **Footer:** docs, changelog, support email, legal (terms, privacy).
