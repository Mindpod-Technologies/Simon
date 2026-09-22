---
name: outreach-sprint
description: Research prospects, draft an honest outreach campaign, send only with approval, track replies, and compound lessons. Approval-gated at every send — the owner's domain reputation is sacred.
---

# Outreach Sprint Procedure

A complete outbound cycle in one loop: research → draft → approve → send →
track → learn. Requires Simon's mailbox (send_email) and web research
tools. If the mailbox is not configured, say so in one line and stop.

1. **Intake** — define before touching anything

Pin these down with the owner (ask only for what is missing):

- **Segment** — who exactly (role, industry, size) and why they care now.
- **Offer** — the one concrete outcome being offered, in one sentence.
- **Leads source** — an uploaded list, web research per named account, or
  an existing CRM/Twin.so export. Never invent leads.
- **Guardrails** — volume cap (default: 20/day max), tone, anything that
  must NOT be said. Domain reputation outranks volume, always.

2. **Research** — earn the right to write

- For named accounts: web_search the company + one relevant person,
  fetch_url their site. Two facts per account, cited.
- For segments: research the segment's current pains, language, and news.
- Call `ingest_note` on the findings (name: `outreach-research-<segment>-<date>`)
  so the knowledge compounds into the next sprint.

3. **Draft** — the campaign document

Use `create_document` with a review-ready campaign:

- A 3-touch sequence (intro → value → polite close). Plain text, no fake
  familiarity, no fake "I saw your…", no hype. Subject lines under 45
  chars, honest about what the email is.
- One clear, low-friction ask (a 15-minute call or a yes/no question).
- The lead table: name, company, email, the two researched facts, status.
- Mark it clearly: **DRAFT — awaiting owner approval**.

Present the document and WAIT. Do not proceed to sends on your own
judgement. "Approve" sends it; edits come first if the owner wants changes.

4. **Send** — approval-gated, small batches

- Only after explicit owner approval for THIS campaign document.
- Send via send_email, in batches that respect the volume cap. State the
  batch size before sending and confirm the exact recipient list.
- After each batch, `ingest_note` the campaign ledger:
  `outreach-ledger-<segment>-<date>` — who got what, when, subject lines.

5. **Reply tracking** — the mail watch works for you

- Watch the inbox for replies matching the campaign subjects (read_recent_emails).
- Classify each reply: **interested** / **question** / **not-now** /
  **negative**. Surface interested + question replies to the owner
  immediately with a suggested response in their voice.
- Unsubscribes and negatives: mark them in the ledger as do-not-contact.
  Never re-mail a do-not-contact.

6. **Review** — lessons compound

On request or weekly: read the ledger, report honestly (sent, replies by
class, best-performing subject), then `ingest_note` the lessons
(`outreach-lessons-<segment>-<date>`): what earned replies, what flopped,
what to change next sprint. Propose the next segment or angle.

## Hard rules

- NEVER send without explicit approval for that exact campaign + list.
- NEVER fabricate a lead, a fact about a lead, or a reply rate.
- NEVER claim a send happened without the tool result proving it.
- Personalization must come from the research notes, not invention.
- If the owner says stop, the campaign stops that turn — no queued sends.
