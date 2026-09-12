---
name: document-review
description: Review an uploaded or referenced document and return structured critique with concrete fixes.
---

# Document Review Procedure

Give documents the read a sharp colleague would. Works with uploaded files
(their chunks appear in context automatically) or files in the workspace —
if the user names a file, find it with the file tools first.

1. **Read the whole thing** — fetch all chunks of the named document before
   judging. Never review from the first page alone.
2. **Classify** — identify the document type (proposal, report, email,
   contract, spec) and its intended audience; judge it against THAT
   audience's needs.
3. **Structured critique**, in this order:
   - **Verdict** — one sentence: ready / ready with fixes / needs rework.
   - **Strengths** — 2–3 bullets; be specific, quote the good line.
   - **Issues** — numbered, each with: what's wrong, why it matters, and
     the concrete fix. Order by severity. Distinguish factual errors
     (flag hardest) from style preferences (label as taste).
   - **Quick wins** — fixes doable in under 5 minutes.
4. **Rewrite on request** — if the user asks, produce the improved version
   (or the fixed section) and save it alongside the original with
   `-revised` in the filename; never overwrite the original.
5. **Reply format** — verdict + issue count + the top 3 issues inline;
   full critique saved to file when it exceeds ~15 lines.

Rules:
- Quote the document when criticizing it ("in section 2 you write…").
- If the document references facts you can check with web tools, offer to
  verify them — don't check uninvited.
