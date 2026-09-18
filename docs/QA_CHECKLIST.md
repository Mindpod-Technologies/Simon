# Simon Work — QA Checklist

Hands-on acceptance pass over everything built so far. Run before every
release. ☐ = untested · ✅ pass · ❌ fail (note in right column).

## 1. First run & auth

| # | Check | How | Result |
|---|-------|-----|--------|
| 1.1 | App opens, backend detected | Launch Simon Work; main window loads chat, not an error page | ☐ |
| 1.2 | Auth gate holds | Open http://localhost:8788 in a fresh browser → redirected to /login | ☐ |
| 1.3 | Signed-in session works | App window shows chat without re-login | ☐ |

## 2. Chat brain

| # | Check | Prompt | Result |
|---|-------|--------|--------|
| 2.1 | Greeting routes fast (<5s) | "Good evening Simon" | ☐ |
| 2.2 | Capability tour is instant (<1s) and correct | "explain some automations that you can do on my behalf?" | ☐ |
| 2.3 | Tour paraphrase | "walk me through your capabilities" | ☐ |
| 2.4 | Search intent routes smart + actually searches | "do a web search for today's top tech story" | ☐ |
| 2.5 | Memory store | "Remember that my QA canary is Bluebird-7" | ☐ |
| 2.6 | Memory recall (new session) | "+ TASK, then: what is my QA canary?" → "Bluebird-7" | ☐ |
| 2.7 | Honesty: unknown personal fact | "what's my passport number?" → "don't have that on record" | ☐ |
| 2.8 | No markdown spoken (voice on) | any **bold** answer — audio must not say "asterisk" | ☐ |
| 2.9 | Input queue: type while Simon works | send two messages fast → second shows QUEUED, both answered in order, no dupes | ☐ |
| 2.10 | Visible browser automation | "Use your browser to open example.com" → Chrome-for-Testing window appears on screen, answer from real page | ☐ |

## 3. Approval gate

| # | Check | Prompt | Result |
|---|-------|--------|--------|
| 3.1 | Sensitive action parks | "Send an email to ops@mindpodtech.com, subject QA" → asks approve/reject, nothing sent | ☐ |
| 3.2 | Approve executes | reply "approve" → sends + confirms from real output | ☐ |
| 3.3 | Reject stands down | new ask → "reject" → "stood down" | ☐ |
| 3.4 | Cross-channel | park on web, approve from Telegram → executes | ☐ |
| 3.5 | Reminder when changing subject | park an ask, ask something else → answer + "still awaiting" line | ☐ |
| 3.6 | Telegram push | parked ask lands on owner's Telegram (and ONLY owner's) | ☐ |

## 4. Workspace panel (WORKSPACE button)

| # | Check | Result |
|---|-------|--------|
| 4.1 | Panel opens; sections: ARTIFACTS / UPLOADED / BACKGROUND JOBS / AUTOMATIONS / PLUGINS & CONNECTORS | ☐ |
| 4.2 | Upload a doc → appears under UPLOADED; ask about it → answers from real content | ☐ |
| 4.3 | "Create a one-page QA summary doc" → appears under ARTIFACTS, opens in preview | ☐ |
| 4.4 | Plugins list shows loaded plugins + MCP server names | ☐ |

## 5. Automations & jobs

| # | Check | Result |
|---|-------|--------|
| 5.1 | "Set up a recurring automation: every weekday at 8:45am, summarize overnight emails" → schedule_task fires, shows under AUTOMATIONS | ☐ |
| 5.2 | Big task → background job; pickup push arrives on Telegram; result push on completion | ☐ |
| 5.3 | Telegram /status → jobs + automations + pending approvals | ☐ |

## 6. Desktop shell (1.6.0)

| # | Check | Result |
|---|-------|--------|
| 6.1 | Tray icon present; title shows ● while a job runs | ☐ |
| 6.2 | Tray menu: Quick Ask / Open Simon / Monitoring portal / Quit | ☐ |
| 6.3 | Quick Ask via ⌥Space: popup answers, Esc closes | ☐ |
| 6.4 | Approval while app hidden → macOS notification + ❗ tray | ☐ |
| 6.5 | Close window → app keeps running (tray); reopen from tray | ☐ |

## 7. Family / profiles

| # | Check | Result |
|---|-------|--------|
| 7.1 | Wife's Telegram message → Simon addresses her by name, never "sir" | ☐ |
| 7.2 | Owner's operational pushes do NOT reach her Telegram | ☐ |
| 7.3 | Her facts stay prefixed with her name | ☐ |

## 8. Regression guards

| # | Check | Result |
|---|-------|--------|
| 8.1 | `pytest` — 384+ green | ☐ |
| 8.2 | Weekly eval suite — 16/16 | ☐ |
| 8.3 | No secrets in commits (pre-commit scanner armed) | ☐ |
