#!/usr/bin/env bash
# Switchboard — pull the latest code from git and recreate the stack.
#
# This is the entire "update production" flow once install.sh has run.
# Run it on the VPS after each `git push` to main.
#
# Usage (from anywhere):
#   bash deploy/update.sh
#
# Optional: drop into cron (auto-pull every 5 min) — see docs/VPS_DEPLOY.md.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DIR/.." && pwd)"
cd "$REPO_ROOT"

echo "==> $(date -u +%FT%TZ)  Updating Switchboard"

# 1. Fetch + fast-forward main. Refuse if local diverged from origin.
echo "==> git pull"
git fetch --tags origin
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" = "$REMOTE" ]; then
    echo "    Already at $REMOTE — nothing to do."
    exit 0
fi
git merge --ff-only origin/main

# 2. Rebuild + recreate. Compose only rebuilds images whose context changed.
echo "==> docker compose up -d --build"
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build --remove-orphans

# 3. Prune any dangling intermediate images to keep disk usage low.
echo "==> docker image prune (dangling)"
docker image prune -f >/dev/null || true

# 4. Show status.
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
echo
echo "==> Done. Live commit:"
git log -1 --oneline
