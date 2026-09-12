#!/usr/bin/env bash
# Simon — macOS installer (v2). One command, local-first, idempotent.
#
#   ./deploy/install_mac.sh                 interactive wizard
#   ./deploy/install_mac.sh --dry-run       print every step, change nothing
#   ./deploy/install_mac.sh --skip-models   skip the ~18 GB model downloads
#   SIMON_NONINTERACTIVE=1 ./deploy/install_mac.sh   accept all defaults
#
# What it does: verifies hardware, installs Homebrew deps + Ollama, pulls
# the two-brain model fleet (gpt-oss:20b + qwen3:8b + nomic-embed-text),
# creates the venv, runs a first-run wizard that writes .env, installs a
# default mcp.json, loads both launchd services (assistant + monitor), and
# health-checks the result.
set -euo pipefail

SIMON_DIR="${SIMON_DIR:-$HOME/simon}"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
DRY_RUN=0
SKIP_MODELS=0
NONINTERACTIVE="${SIMON_NONINTERACTIVE:-0}"

for arg in "$@"; do
    case "$arg" in
        --dry-run)      DRY_RUN=1 ;;
        --skip-models)  SKIP_MODELS=1 ;;
        --noninteractive) NONINTERACTIVE=1 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERR\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [[ $DRY_RUN == 1 ]]; then printf '   [dry-run] %s\n' "$*"; else eval "$@"; fi; }

# ask <var> <prompt> [default] — wizard prompt honoring --noninteractive.
ask() {
    local var="$1" prompt="$2" default="${3:-}"
    if [[ $NONINTERACTIVE == 1 || ! -t 0 ]]; then
        printf -v "$var" '%s' "$default"; return
    fi
    local shown=""; [[ -n "$default" ]] && shown=" [$default]"
    read -r -p "    $prompt$shown: " reply || true
    printf -v "$var" '%s' "${reply:-$default}"
}

# ---------------------------------------------------------------------------
log "Simon installer (v2) — local-first setup"
[[ $DRY_RUN == 1 ]] && warn "DRY RUN — nothing will be changed."

# --- 1. Platform & hardware -------------------------------------------------
[[ "$(uname -s)" == "Darwin" ]] || die "macOS only. For Linux use deploy/install_vps.sh."
[[ "$(uname -m)" == "arm64" ]] || warn "Intel Mac detected — local models need Apple Silicon; plan on a cloud LLM (the wizard offers it)."

RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
log "Detected ${RAM_GB} GB unified memory."
if (( RAM_GB < 16 )); then
    warn "Under 16 GB — local models will not fit; choose the cloud option in the wizard."
elif (( RAM_GB < 24 )); then
    warn "16-24 GB — the smart brain (gpt-oss:20b, 13 GB) fits but leave headroom; close heavy apps."
fi

# --- 2. Homebrew ------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
    if [[ $DRY_RUN == 1 ]]; then
        warn "[dry-run] would install Homebrew"
    else
        log "Installing Homebrew (Apple's prompt may ask for your password)..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || true)"
    fi
fi
command -v brew >/dev/null 2>&1 || [[ $DRY_RUN == 1 ]] || die "Homebrew missing after install attempt."

# --- 3. System dependencies -------------------------------------------------
log "System dependencies (python@3.11, ffmpeg, portaudio, ollama)..."
for pkg in python@3.11 ffmpeg portaudio ollama; do
    if brew list "$pkg" >/dev/null 2>&1; then
        printf '    ok: %s\n' "$pkg"
    else
        run "brew install $pkg"
    fi
done

PY311="$(brew --prefix python@3.11 2>/dev/null)/bin/python3.11"
[[ -x "$PY311" ]] || PY311="$(command -v python3.11 || true)"
[[ -n "$PY311" ]] || [[ $DRY_RUN == 1 ]] || die "python3.11 not found."

# --- 4. Repo ----------------------------------------------------------------
if [[ ! -f "$SIMON_DIR/run.py" ]]; then
    die "Expected the Simon repo at $SIMON_DIR (with run.py).
     Clone it first:  git clone <repo-url> $SIMON_DIR
     (or point elsewhere with SIMON_DIR=/path ./deploy/install_mac.sh)"
fi
cd "$SIMON_DIR"

# --- 5. Ollama service ------------------------------------------------------
if command -v ollama >/dev/null 2>&1; then
    if ! curl -sf --max-time 3 http://localhost:11434/api/version >/dev/null 2>&1; then
        log "Starting Ollama..."
        run "brew services start ollama" || run "(ollama serve >/dev/null 2>&1 &)"
        sleep 3
    fi
    curl -sf --max-time 3 http://localhost:11434/api/version >/dev/null 2>&1 \
        && log "Ollama is up." || warn "Ollama not responding yet — model pulls may fail; re-run with --skip-models off later."
fi

# --- 6. First-run wizard ----------------------------------------------------
ENV_FILE="$SIMON_DIR/.env"
WANT_MODELS=1
if [[ ! -f "$ENV_FILE" ]]; then
    log "First-run wizard — writing .env"
    ask LLM_CHOICE "Brain: (l)ocal models [free/private] or (c)loud API" "l"
    case "$LLM_CHOICE" in [cC]*) _cloud=1 ;; *) _cloud=0 ;; esac
    if [[ $_cloud == 1 ]]; then
        WANT_MODELS=0
        ask LLM_BASE_URL "LLM base URL" "https://api.openai.com/v1"
        ask LLM_API_KEY  "LLM API key" ""
        ask LLM_MODEL    "Model name" "gpt-4o-mini"
        ROUTER=false; FAST=""
    else
        LLM_BASE_URL="http://localhost:11434/v1"; LLM_API_KEY=""
        LLM_MODEL="gpt-oss:20b"; FAST="qwen3:8b"; ROUTER=true
    fi
    ask TG_TOKEN   "Telegram bot token (blank = skip Telegram)" ""
    TG_IDS=""
    [[ -n "$TG_TOKEN" ]] && ask TG_IDS "Your Telegram user ID (numeric)" ""
    ask SLACK_BOT  "Slack bot token xoxb-… (blank = skip Slack)" ""
    SLACK_APP=""; SLACK_IDS=""
    if [[ -n "$SLACK_BOT" ]]; then
        ask SLACK_APP "Slack app token xapp-…" ""
        ask SLACK_IDS "Allowed Slack user IDs (comma-separated)" ""
    fi
    ask SIMON_LICENSE_KEY "License key (blank = free trial)" ""

    if [[ $DRY_RUN == 1 ]]; then
        warn "[dry-run] would write $ENV_FILE"
    else
        cp .env.example .env
        # Portable in-place set: KEY=value (values may be empty).
        set_kv() { /usr/bin/sed -i '' "s|^$1=.*|$1=$2|" .env; }
        set_kv LLM_BASE_URL "$LLM_BASE_URL"
        set_kv LLM_API_KEY "$LLM_API_KEY"
        set_kv LLM_MODEL "$LLM_MODEL"
        set_kv LLM_MODEL_FAST "$FAST"
        set_kv LLM_ROUTER_ENABLED "$ROUTER"
        set_kv TELEGRAM_BOT_TOKEN "$TG_TOKEN"
        set_kv TELEGRAM_ALLOWED_USER_IDS "$TG_IDS"
        set_kv SLACK_BOT_TOKEN "$SLACK_BOT"
        set_kv SLACK_APP_TOKEN "$SLACK_APP"
        set_kv SLACK_ALLOWED_USER_IDS "$SLACK_IDS"
        set_kv SIMON_LICENSE_KEY "$SIMON_LICENSE_KEY"
        log ".env written ($ENV_FILE)."
    fi
else
    log ".env already exists — leaving it untouched."
    grep -q "localhost:11434" "$ENV_FILE" || WANT_MODELS=0
fi

# --- 7. Model fleet ---------------------------------------------------------
if [[ $SKIP_MODELS == 1 || $WANT_MODELS == 0 ]]; then
    [[ $SKIP_MODELS == 1 ]] && warn "Skipping model downloads (--skip-models)."
else
    for m in gpt-oss:20b qwen3:8b nomic-embed-text; do
        if ollama list 2>/dev/null | grep -q "^$m"; then
            printf '    ok: %s already pulled\n' "$m"
        else
            log "Pulling $m (this is the slow part — gpt-oss is ~13 GB)..."
            run "ollama pull $m"
        fi
    done
fi

# --- 8. Python venv ---------------------------------------------------------
if [[ ! -x .venv/bin/python ]]; then
    log "Creating virtualenv..."
    run "\"$PY311\" -m venv .venv"
fi
log "Installing Python dependencies..."
run ".venv/bin/pip install --upgrade pip -q"
run ".venv/bin/pip install -q -r requirements.txt"

# --- 9. Default mcp.json (filesystem server over the workspace) -------------
if [[ ! -f mcp.json ]]; then
    log "Writing default mcp.json (filesystem tools over ./workspace)..."
    NPX="$(command -v npx || echo /opt/homebrew/bin/npx)"
    if [[ $DRY_RUN == 1 ]]; then
        warn "[dry-run] would write mcp.json"
    else
        cat > mcp.json <<EOF
{
  "servers": {
    "filesystem": {
      "command": "$NPX",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "$SIMON_DIR/workspace"],
      "env": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
    }
  }
}
EOF
    fi
fi
mkdir -p "$SIMON_DIR/workspace" "$SIMON_DIR/data/logs"

# --- 10. launchd services ---------------------------------------------------
mkdir -p "$LAUNCH_AGENTS"
UID_DOMAIN="gui/$(id -u)"
for label in com.simon.assistant com.simon.monitor; do
    log "Installing $label ..."
    if [[ $DRY_RUN == 1 ]]; then
        warn "[dry-run] would render + load $label.plist"
        continue
    fi
    sed "s|__HOME__|$HOME|g" "$SIMON_DIR/deploy/$label.plist" > "$LAUNCH_AGENTS/$label.plist"
    if launchctl list "$label" >/dev/null 2>&1; then
        # Already loaded: restart in place — no bootout/bootstrap race.
        launchctl kickstart -k "$UID_DOMAIN/$label" \
            && printf '    restarted: %s\n' "$label" \
            || warn "kickstart failed for $label — check: launchctl list $label"
    else
        if ! launchctl bootstrap "$UID_DOMAIN" "$LAUNCH_AGENTS/$label.plist" 2>/dev/null; then
            sleep 2  # bootout/bootstrap race guard
            launchctl bootstrap "$UID_DOMAIN" "$LAUNCH_AGENTS/$label.plist" \
                && printf '    loaded: %s\n' "$label" \
                || warn "bootstrap failed for $label — load manually:
       launchctl bootstrap $UID_DOMAIN $LAUNCH_AGENTS/$label.plist"
        else
            printf '    loaded: %s\n' "$label"
        fi
    fi
done

# --- 11. Health check -------------------------------------------------------
if [[ $DRY_RUN == 0 ]]; then
    sleep 5
    WEB=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8788/ || true)
    MON=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8789/ || true)
    [[ "$WEB" == 200 ]] && log "Web UI healthy."  || warn "Web UI not answering yet — check data/logs/simon.err.log"
    [[ "$MON" == 200 ]] && log "Monitor healthy." || warn "Monitor not answering yet — check data/logs/"

    cat <<MSG

┌──────────────────────────────────────────────────────────────┐
│  Simon is installed and running.                             │
│                                                              │
│  Web chat   http://localhost:8788                            │
│  Monitor    http://localhost:8789                            │
│  Logs       tail -f $SIMON_DIR/data/logs/simon.out.log
│  Restart    launchctl kickstart -k gui/\$(id -u)/com.simon.assistant
│                                                              │
│  Tips: keep the Mac awake (sudo pmset -a sleep 0), enable    │
│  automatic login, and see deploy/tailscale.md for remote     │
│  access. Edit .env to add Telegram/Slack later, then restart.│
└──────────────────────────────────────────────────────────────┘
MSG
else
    log "Dry run complete — re-run without --dry-run to install."
fi
