#!/usr/bin/env bash
# chrome_debug.sh — start your REAL Chrome in "Simon takeover" mode.
# Simon (SIMON_BROWSER_CDP_URL=http://localhost:9222) can then drive YOUR
# session: your tabs, your logins. Close Chrome normally to end sharing.
#
# Uses a COPY of your profile dir so your everyday Chrome can stay open.
set -euo pipefail
PORT="${1:-9222}"
PROFILE_SRC="$HOME/Library/Application Support/Google/Chrome"
PROFILE_COPY="$HOME/simon/data/chrome-takeover-profile"
mkdir -p "$HOME/simon/data"
if [ ! -d "$PROFILE_COPY" ]; then
    echo "Copying your Chrome profile (one-time, may take a minute)…"
    rsync -a --delete "$PROFILE_SRC/Default" "$PROFILE_COPY/Default" 2>/dev/null || true
    rsync -a "$PROFILE_SRC/Local State" "$PROFILE_COPY/" 2>/dev/null || true
fi
echo "Launching Chrome (takeover mode) on :$PORT — Simon can see these tabs."
exec open -na "Google Chrome" --args \
    --remote-debugging-port="$PORT" \
    --user-data-dir="$PROFILE_COPY" \
    --no-first-run
