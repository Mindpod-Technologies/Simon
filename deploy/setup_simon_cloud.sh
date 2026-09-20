#!/usr/bin/env bash
# setup_simon_cloud.sh — one-paste Simon Cloud bootstrap for a fresh VPS.
#
# Usage (as root, Ubuntu/Debian):
#   export SIMON_CLOUD_DOMAIN=simonwork.app
#   export SIMON_LICENSE_PRIVATE_KEY=<vendor ed25519 hex>
#   export LLM_API_KEY=<hosted frontier pool key>
#   curl -fsSL https://raw.githubusercontent.com/Mindpod-Technologies/Simon/main/deploy/setup_simon_cloud.sh | bash
#
# What it does: Docker + Caddy, wildcard-DNS pre-flight, host env, Simon
# image build, billing service (systemd), main Caddyfile with tenant import.
# Idempotent: safe to re-run; existing config is preserved, not clobbered.

set -euo pipefail

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mXX\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "run as root (or via sudo bash)"
command -v apt-get >/dev/null || die "this bootstrap targets Ubuntu/Debian"

CLOUD_ROOT="${SIMON_CLOUD_ROOT:-/opt/simon-cloud}"
SRC_DIR="$CLOUD_ROOT/simon-src"
DOMAIN="${SIMON_CLOUD_DOMAIN:-}"
HOST_ENV="$CLOUD_ROOT/host.env"

say "Simon Cloud bootstrap — domain: ${DOMAIN:-<unset>}"

# --- 1. Docker --------------------------------------------------------------
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    say "Docker already present — skipping"
else
    say "Installing Docker…"
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg >/dev/null
    install -m 0755 -d /etc/apt/keyrings
    [ -f /etc/apt/keyrings/docker.asc ] || \
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
fi

# --- 2. Caddy ---------------------------------------------------------------
if command -v caddy >/dev/null; then
    say "Caddy already present — skipping"
else
    say "Installing Caddy…"
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https >/dev/null
    curl -fsSL "https://dl.cloudsmith.io/public/caddy/stable/gpg.key" \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null || true
    curl -fsSL "https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt" \
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy >/dev/null
fi

# --- 3. Wildcard DNS pre-flight ---------------------------------------------
if [ -n "$DOMAIN" ]; then
    say "Checking wildcard DNS for *.$DOMAIN…"
    PUBLIC_IP="$(curl -fsSL -m 10 https://api.ipify.org || true)"
    RESOLVED="$(getent hosts "bootstrap-check.$DOMAIN" | awk '{print $1}' | head -1 || true)"
    if [ -z "$RESOLVED" ]; then
        warn "*.$DOMAIN does not resolve yet — add an A record for * and $DOMAIN pointing at ${PUBLIC_IP:-<this VPS>} before tenants go live."
    elif [ "$RESOLVED" != "$PUBLIC_IP" ]; then
        warn "*.$DOMAIN resolves to $RESOLVED but this box is $PUBLIC_IP — fix DNS before tenants go live."
    else
        say "Wildcard DNS resolves to this VPS ✓"
    fi
else
    warn "SIMON_CLOUD_DOMAIN unset — set it in $HOST_ENV before provisioning tenants."
fi

# --- 4. Layout + host env ----------------------------------------------------
say "Preparing $CLOUD_ROOT…"
mkdir -p "$CLOUD_ROOT/tenants" "$CLOUD_ROOT/logs"
if [ ! -f "$HOST_ENV" ]; then
    cat > "$HOST_ENV" <<EOF
# Simon Cloud host configuration
SIMON_CLOUD_DOMAIN=${DOMAIN}
SIMON_CLOUD_ROOT=$CLOUD_ROOT
SIMON_IMAGE=simon-cloud:latest
SIMON_LICENSE_PRIVATE_KEY=${SIMON_LICENSE_PRIVATE_KEY:-}
LLM_BASE_URL=${LLM_BASE_URL:-https://api.moonshot.ai/v1}
LLM_MODEL=${LLM_MODEL:-kimi-k3}
LLM_MODEL_FAST=${LLM_MODEL_FAST:-kimi-k3}
LLM_API_KEY=${LLM_API_KEY:-}
# Billing service (Stripe → license keys + auto-provisioning):
SIMON_CLOUD_PROVISION=1
STRIPE_WEBHOOK_SECRET=${STRIPE_WEBHOOK_SECRET:-}
STRIPE_PAYMENT_LINK_PLANS=${STRIPE_PAYMENT_LINK_PLANS:-}
EOF
    chmod 600 "$HOST_ENV"
    say "Wrote $HOST_ENV (chmod 600) — fill in the blanks"
else
    say "Host env exists — keeping it"
fi

# --- 5. Simon image -----------------------------------------------------------
say "Building the Simon image (public repo clone)…"
if [ ! -d "$SRC_DIR/.git" ]; then
    git clone --depth 1 https://github.com/Mindpod-Technologies/Simon.git "$SRC_DIR"
else
    git -C "$SRC_DIR" pull --ff-only || warn "source update skipped (offline?)"
fi
docker build -q -t simon-cloud:latest -f "$SRC_DIR/deploy/Dockerfile" "$SRC_DIR" >/dev/null
say "Image built: simon-cloud:latest"

# --- 6. Billing service (systemd) ---------------------------------------------
say "Installing the billing service…"
if [ ! -d "$CLOUD_ROOT/billing-venv" ]; then
    python3 -m venv "$CLOUD_ROOT/billing-venv"
    "$CLOUD_ROOT/billing-venv/bin/pip" -q install --upgrade pip
    "$CLOUD_ROOT/billing-venv/bin/pip" -q install fastapi uvicorn pydantic pydantic-settings cryptography requests
fi
cat > /etc/systemd/system/simon-billing.service <<EOF
[Unit]
Description=Simon Cloud billing (Stripe → license keys → tenants)
After=network.target docker.service

[Service]
EnvironmentFile=$HOST_ENV
WorkingDirectory=$SRC_DIR
ExecStart=$CLOUD_ROOT/billing-venv/bin/python -m billing.server
Restart=always
RestartSec=5
StandardOutput=append:$CLOUD_ROOT/logs/billing.out.log
StandardError=append:$CLOUD_ROOT/logs/billing.err.log

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now simon-billing.service

# --- 7. Caddy main config ------------------------------------------------------
if [ -n "$DOMAIN" ]; then
    say "Writing Caddyfile (billing endpoint + tenant import)…"
    cat > /etc/caddy/Caddyfile <<EOF
# Simon Cloud — billing webhook + success pages
billing.$DOMAIN {
    reverse_proxy 127.0.0.1:8792
}

# Per-tenant apps (auto-HTTPS per subdomain)
import $CLOUD_ROOT/tenants/*/Caddyfile
EOF
    systemctl reload caddy || systemctl restart caddy
fi

say "Done. Next steps:"
echo "  1. Fill $HOST_ENV (license private key, LLM key, Stripe secret)"
echo "  2. Stripe webhook → https://billing.${DOMAIN:-<domain>}/stripe/webhook"
echo "     Payment Link success URL → https://billing.${DOMAIN:-<domain>}/success?session_id={CHECKOUT_SESSION_ID}"
echo "  3. Smoke test:  python3 $SRC_DIR/deploy/provision_tenant.py provision --email you@yourco.com --plan pro"
echo "     then open the printed https://<slug>.$DOMAIN URL"
echo "  4. Logs: $CLOUD_ROOT/logs/ · billing: journalctl -u simon-billing"
