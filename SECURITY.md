# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 1.6.x   | ✅ current |
| < 1.6   | ❌ upgrade first |

## Reporting a Vulnerability

**Do not open a public issue.** Email **security@mindpodtech.com** with:

- a description of the issue and its impact
- reproduction steps or a proof of concept
- affected version(s)

You should get an acknowledgment within 48 hours and a triage decision
within 7 days. If the report is accepted, we ship a fix and credit you in
the release notes (unless you'd rather stay anonymous).

## Scope notes for researchers

Simon is a local-first agent that runs with broad local permissions **by
design** — that is the product. We consider these in scope anyway:

- Secrets or credentials leaking into the repo, releases, logs, or
  telemetry (there is no telemetry — if you find any, that's a bug)
- License-key forgery or signature bypass (`simon/licensing.py`)
- Auth-gate bypass on the web UI (`simon/auth.py`, `simon/interfaces/web.py`)
- Approval-gate bypass (`simon/approvals.py`) — a sensitive action running
  without an explicit owner decision
- Sandboxing escapes in tool execution, MCP tool confinement issues
- Remote attack surface: anything reachable without authentication on
  ports 8788–8792 beyond the documented public paths

Out of scope: "the agent did something dumb with permissions I granted it"
(prompt-level behavior), denial of service via your own local models, and
issues in third-party MCP servers you configured yourself.

## Our side of the deal

- All signing keys live outside this repository; the public key only is
  shipped. See `simon/licensing.py`.
- `.env`, `mcp.json`, and any credential file are never committed; the repo
  is scanned before pushes (`scripts/secret-scan.sh`).
- GitHub secret scanning + push protection are enabled on the repository.
