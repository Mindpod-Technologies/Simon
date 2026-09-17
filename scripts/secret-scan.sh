#!/usr/bin/env bash
# secret-scan.sh — refuse to leak credentials into the repo.
#
# Scans staged changes (or full history with --history) for token/password
# patterns. Wire it as a pre-commit hook:
#   ln -sf ../../scripts/secret-scan.sh .git/hooks/pre-commit
#
# Exit 0 = clean, 1 = potential secret found (prints the hits).

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

PATTERNS='(github_pat_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|xoxb-[0-9]{6,}|xapp-[0-9]|sk-ant-[A-Za-z0-9-]{20,}|sk-[A-Za-z0-9]{20,}|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}|whsec_[A-Za-z0-9]{8,}|BEGIN [A-Z ]*PRIVATE KEY)'

if [ "${1:-}" = "--history" ]; then
    echo "Scanning full git history…"
    if git rev-list --all | xargs -I{} git grep -nE "$PATTERNS" {} 2>/dev/null | grep -v '\.example\|SECURITY.md\|secret-scan'; then
        echo "POTENTIAL SECRETS IN HISTORY (above)"; exit 1
    fi
else
    echo "Scanning staged changes…"
    if git diff --cached | grep -nE "$PATTERNS" | grep -v '\.example\|SECURITY.md\|secret-scan'; then
        echo "POTENTIAL SECRET IN STAGED CHANGES (above) — unstage it."
        exit 1
    fi
fi
echo "clean"
