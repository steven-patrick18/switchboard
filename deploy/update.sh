#!/usr/bin/env bash
# Switchboard — pull the latest code from git and recreate the stack.
#
# Two trigger paths:
#   1. Sentinel file present at $SENTINEL_PATH — the operator clicked
#      "Apply update" in the Settings GUI. Run immediately, then
#      delete the sentinel so the GUI flips back to "up to date".
#   2. Nothing pending — fall back to a normal git-ahead check; pull
#      and rebuild only if origin/main moved.
#
# Safe to run from cron (e.g. every 5 min):
#   */5 * * * * /opt/switchboard/deploy/update.sh >> /var/log/switchboard-update.log 2>&1

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DIR/.." && pwd)"
SENTINEL_PATH="${SWITCHBOARD_SENTINEL:-/var/run/switchboard/update-requested}"

cd "$REPO_ROOT"

echo "==> $(date -u +%FT%TZ)  Switchboard update check"

TRIGGERED_BY_GUI=0
if [ -f "$SENTINEL_PATH" ]; then
    TRIGGERED_BY_GUI=1
    echo "==> Sentinel found: $SENTINEL_PATH"
    cat "$SENTINEL_PATH" || true
fi

git fetch --tags origin >/dev/null
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ] && [ "$TRIGGERED_BY_GUI" -eq 0 ]; then
    echo "    Already at $REMOTE — nothing to do."
    exit 0
fi

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "==> git pull (local $LOCAL → remote $REMOTE)"
    git merge --ff-only origin/main
fi

# Rebuild + recreate. Compose skips images whose context didn't change.
echo "==> docker compose up -d --build"
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build --remove-orphans

echo "==> docker image prune (dangling)"
docker image prune -f >/dev/null || true

# Clear the sentinel so the GUI flips from "queued" back to "up to date".
if [ "$TRIGGERED_BY_GUI" -eq 1 ]; then
    rm -f "$SENTINEL_PATH" || true
    echo "==> Sentinel cleared."
fi

docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
echo
echo "==> Done. Live commit:"
git log -1 --oneline
