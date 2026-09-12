#!/usr/bin/env bash
# Simon — Debian/Ubuntu VPS installer. Idempotent; safe to re-run.
# Option A (default): Docker. Option B: systemd. Pass --systemd to force B.
set -euo pipefail

MODE=""
[[ "${1:-}" == "--systemd" ]] && MODE="systemd"
[[ "${1:-}" == "--docker" ]] && MODE="docker"

log() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f /etc/debian_version ]]; then
    echo "This installer targets Debian/Ubuntu." >&2
    exit 1
fi

# --- Choose mode ------------------------------------------------------------
if [[ -z "$MODE" ]]; then
    if command -v docker >/dev/null 2>&1; then
        MODE="docker"
    else
        # Default to docker when neither is forced; we can install it.
        MODE="docker"
    fi
fi
log "Install mode: $MODE"

# --- Option A: Docker --------------------------------------------------------
install_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        log "Installing Docker..."
        sudo apt-get update
        sudo apt-get install -y ca-certificates curl
        curl -fsSL https://get.docker.com | sudo sh
        sudo usermod -aG docker "$USER" || true
        log "Docker installed. You may need to log out/in for group membership."
    fi

    cd "$REPO_ROOT"
    if [[ ! -f .env ]]; then
        log "Creating .env from .env.example — EDIT IT before Simon will work."
        cp .env.example .env
    fi

    log "Building and starting Simon (docker compose)..."
    if docker compose version >/dev/null 2>&1; then
        sudo docker compose -f deploy/docker-compose.yml up -d --build
    else
        sudo docker-compose -f deploy/docker-compose.yml up -d --build
    fi

    cat <<MSG

Simon is running in Docker.
  - Web UI:   http://<server-ip>:8788
  - Config:   edit $REPO_ROOT/.env, then: sudo docker compose -f deploy/docker-compose.yml restart
  - Logs:     sudo docker compose -f deploy/docker-compose.yml logs -f
  - Remote access: see deploy/tailscale.md
MSG
}

# --- Option B: systemd -------------------------------------------------------
install_systemd() {
    log "Installing system packages (python3.11, ffmpeg, rsync)..."
    sudo apt-get update
    sudo apt-get install -y python3.11 python3.11-venv python3-pip ffmpeg rsync

    log "Syncing repo to /opt/simon..."
    sudo mkdir -p /opt/simon
    sudo rsync -a --delete --exclude .venv --exclude data --exclude .git \
        "$REPO_ROOT/" /opt/simon/

    log "Creating virtualenv and installing dependencies..."
    if [[ ! -x /opt/simon/.venv/bin/python ]]; then
        sudo python3.11 -m venv /opt/simon/.venv
    fi
    sudo /opt/simon/.venv/bin/pip install --upgrade pip
    sudo /opt/simon/.venv/bin/pip install -r /opt/simon/requirements.txt

    if [[ ! -f /opt/simon/.env ]]; then
        log "Creating /opt/simon/.env from .env.example — EDIT IT."
        sudo cp /opt/simon/.env.example /opt/simon/.env
    fi
    sudo mkdir -p /opt/simon/data/logs /opt/simon/workspace

    log "Installing systemd unit..."
    sudo cp deploy/simon.service /etc/systemd/system/simon.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now simon

    cat <<MSG

Simon is running via systemd.
  - Status:   sudo systemctl status simon
  - Logs:     sudo journalctl -u simon -f
  - Config:   edit /opt/simon/.env, then: sudo systemctl restart simon
  - Web UI:   http://<server-ip>:8788
  - Remote access: see deploy/tailscale.md
MSG
}

case "$MODE" in
    docker)  install_docker ;;
    systemd) install_systemd ;;
    *) echo "Unknown mode: $MODE" >&2; exit 1 ;;
esac
