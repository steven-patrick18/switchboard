#!/usr/bin/env bash
# Switchboard — first-time install on a fresh Ubuntu/Debian VPS.
#
# Idempotent: re-running it is safe. Generates secrets the first time,
# then builds and starts the Docker stack.
#
# Usage:
#   cd switchboard/deploy
#   bash install.sh

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DIR/.." && pwd)"
cd "$DIR"

echo "==> Switchboard install"

# --- Docker -----------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    echo "==> Installing Docker (one-time)..."
    curl -fsSL https://get.docker.com | sh
    # Add current user to docker group so subsequent docker calls don't need sudo.
    if [ -n "${SUDO_USER:-}" ]; then
        usermod -aG docker "$SUDO_USER" || true
    fi
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: docker compose plugin not found. Reinstall Docker via get.docker.com."
    exit 1
fi

# --- .env -------------------------------------------------------------------
if [ ! -f .env ]; then
    echo "==> Creating deploy/.env from .env.example"
    cp .env.example .env
fi

# Generate POSTGRES_PASSWORD + APP_SECRET_KEY if blank.
gen_secret() { openssl rand -base64 48 | tr -d '\n=' | cut -c1-48; }

if grep -qE '^POSTGRES_PASSWORD=$' .env; then
    pw=$(gen_secret)
    sed -i "s|^POSTGRES_PASSWORD=$|POSTGRES_PASSWORD=$pw|" .env
    echo "    POSTGRES_PASSWORD generated."
fi
if grep -qE '^APP_SECRET_KEY=$' .env; then
    sk=$(gen_secret)
    sed -i "s|^APP_SECRET_KEY=$|APP_SECRET_KEY=$sk|" .env
    echo "    APP_SECRET_KEY generated (DO NOT change this after storing credentials)."
fi

# --- Sanity-check domains ---------------------------------------------------
# shellcheck disable=SC1091
set -a; . ./.env; set +a

if [ "${API_DOMAIN:-}" = "" ] || [ "${API_DOMAIN}" = "api.your-domain.com" ]; then
    echo
    echo "Almost there. Edit deploy/.env and set:"
    echo "    API_DOMAIN     e.g.  api.your-domain.com"
    echo "    WEB_DOMAIN     e.g.  app.your-domain.com"
    echo "    ACME_EMAIL     e.g.  ops@your-domain.com"
    echo
    echo "Make sure DNS A records for both subdomains point at THIS VPS"
    echo "before re-running this script. Caddy needs DNS to resolve to"
    echo "obtain Let's Encrypt certificates."
    echo
    echo "Then run:  bash deploy/install.sh"
    exit 0
fi

# --- Host directory for the GUI-triggered update sentinel ------------------
# The api container bind-mounts this so the "Apply update" button in the
# Settings GUI can drop a flag file the host's update.sh cron picks up.
if [ ! -d /var/run/switchboard ]; then
    echo "==> Creating /var/run/switchboard for GUI update sentinel"
    mkdir -p /var/run/switchboard
    chmod 1777 /var/run/switchboard  # sticky world-writable like /tmp
fi

# --- Build + start ----------------------------------------------------------
echo "==> Building and starting the stack"
docker compose --env-file .env -f docker-compose.yml up -d --build --remove-orphans

echo "==> Containers"
docker compose --env-file .env -f docker-compose.yml ps

echo
echo "Stack is starting. Caddy is fetching Let's Encrypt certs; this takes"
echo "30-90 seconds the first time. When it's done, visit:"
echo "    https://${WEB_DOMAIN}    (operator UI — sign up for your first account)"
echo "    https://${API_DOMAIN}/health    (should return {\"status\":\"ok\",...})"
echo
echo "Tail logs with:    docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f"
echo "Update after git push:    bash deploy/update.sh"
echo
echo "Recommended: enable the auto-update cron so the GUI's 'Apply update'"
echo "button works without SSH. Run:"
echo
echo "    (crontab -l 2>/dev/null; echo '*/5 * * * * $REPO_ROOT/deploy/update.sh >> /var/log/switchboard-update.log 2>&1') | crontab -"
echo
echo "Once the cron is in place, Settings → System updates can deploy"
echo "the latest commit on its next 5-minute tick."
