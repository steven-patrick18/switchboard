#!/usr/bin/env bash
# Switchboard — daily backup. Dumps Postgres + tars the documents volume.
#
# Drop this in a cron job for daily off-site backups:
#   0 3 * * * /home/ops/switchboard/deploy/backup.sh /home/ops/backups
#
# Keeps the last 14 daily snapshots; older ones are pruned.

set -euo pipefail

DEST="${1:-/var/backups/switchboard}"
mkdir -p "$DEST"

STAMP=$(date -u +%Y%m%d-%H%M%S)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load DB password from deploy/.env.
# shellcheck disable=SC1091
set -a; . "$DIR/.env"; set +a

echo "==> Postgres dump → $DEST/db-${STAMP}.sql.gz"
docker compose --env-file "$DIR/.env" -f "$DIR/docker-compose.yml" exec -T postgres \
    pg_dump -U switchboard switchboard \
    | gzip > "$DEST/db-${STAMP}.sql.gz"

echo "==> Documents tar → $DEST/documents-${STAMP}.tar.gz"
# Mount the named volume into a throwaway container, tar from there.
docker run --rm \
    -v switchboard_documents_data:/data:ro \
    -v "$DEST":/backup \
    alpine \
    tar -czf "/backup/documents-${STAMP}.tar.gz" -C /data .

echo "==> Pruning backups older than 14 days"
find "$DEST" -maxdepth 1 -name 'db-*.sql.gz' -mtime +14 -delete
find "$DEST" -maxdepth 1 -name 'documents-*.tar.gz' -mtime +14 -delete

echo "==> Done"
ls -lh "$DEST" | tail -10
