---
name: follow-up-tracker
description: Capture "waiting on" items and make sure nothing you're owed slips through the cracks.
---

# Follow-Up Tracker Procedure

Track everything the user is WAITING on — the mirror image of a to-do list.

1. **Capture** — when the user mentions waiting on something ("still
   haven't heard from the contractor", "she owes me the slides"), store it
   as a remembered fact: `waiting_on_<topic>: <who/what, since when>`.
   Confirm what was captured in one line.
2. **Set the chase** — ask when to follow up if the user didn't say;
   create a reminder for that date. Reasonable defaults when asked to
   decide: 3 business days for active threads, 1 week for slow ones.
3. **Review** — when the user asks "what am I waiting on?", list all
   `waiting_on_*` facts oldest-first with age ("6 days"). Suggest which
   ones are overdue a nudge.
4. **Close** — when the user says something arrived or resolved, delete or
   update the fact and cancel any pending chase reminder. Confirm closure.

Rules:
- Every tracked item has an owner (who you're waiting on) and a date —
  items without a follow-up date get one proposed, not assumed silently.
- Keep the review list under 10 lines; group by person when items pile up.
- Never nag unprompted more than the reminders the user approved.
