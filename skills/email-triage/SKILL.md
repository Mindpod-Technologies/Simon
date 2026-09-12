---
name: email-triage
description: Scan the inbox, sort what matters, and draft the replies worth sending.
---

# Email Triage Procedure

Turn an overflowing inbox into a short decision list. Requires email to be
configured (IMAP/SMTP) — if the email tools are unavailable, say so in one
line and stop; do not improvise inbox contents.

1. **Fetch** — pull recent unread messages (default: last 24 hours or the
   20 most recent, whichever the user implies).
2. **Sort into three buckets**:
   - **Needs you** — real people expecting a decision or reply.
   - **FYI** — worth knowing, no action.
   - **Noise** — newsletters, automated alerts, marketing. Summarize these
     as a one-line count ("7 newsletters, 3 alerts"), never one by one.
3. **Present** — for each "Needs you" message: sender, subject, the core
   ask in one sentence, and a suggested response in one sentence.
4. **Draft on request** — when the user picks one, draft the reply in their
   voice (check remembered facts for tone/preferences). Never SEND without
   explicit confirmation.
5. **Follow-ups** — offer to set reminders for any thread that needs
   chasing ("if they don't reply by Thursday").

Rules:
- Respect the sender's intent: urgency in the subject beats your guess.
- Never summarize an email you did not actually fetch.
- Keep the triage list under 15 lines; detail lives in the drafts.
