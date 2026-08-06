#!/bin/bash
# ───────────────────────────────────────────────────────────────────
# backup.sh — local backup of Postgres (entries/transcripts) + MinIO
# (audio/video files), run on a schedule via
# ~/Library/LaunchAgents/com.soliloquy.backup.plist (not tracked in
# the repo -- machine-specific, like the mDNS/dnsmasq LaunchAgents).
#
# This is a LOCAL backup only -- a second copy on the same disk as
# the originals. It protects against accidental deletes (docker
# compose down -v, a bad DELETE, etc) but NOT against this Mac's disk
# actually failing. See README.md's "Backups" section for the
# real-redundancy options (free-tier cloud storage) this doesn't
# cover on its own.
# ───────────────────────────────────────────────────────────────────

set -euo pipefail

BACKUP_ROOT="${SOLILOQUY_BACKUP_ROOT:-$HOME/soliloquy-backups}"
KEEP=14  # daily backups to retain; older ones are pruned each run
POSTGRES_CONTAINER="landonkea-soliloquy-postgres-1"
POSTGRES_USER="soliloquy"
POSTGRES_DB="soliloquy"
MINIO_RCLONE_REMOTE="soliloquy-minio:soliloquy"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_ROOT/$TIMESTAMP"
LOG_FILE="$BACKUP_ROOT/backup.log"

mkdir -p "$DEST"
exec >> "$LOG_FILE" 2>&1
echo "── $(date) : starting backup to $DEST"

if ! docker ps --format '{{.Names}}' | grep -qx "$POSTGRES_CONTAINER"; then
    echo "Postgres container ($POSTGRES_CONTAINER) not running -- skipping this backup run."
    exit 1
fi

docker exec "$POSTGRES_CONTAINER" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$DEST/postgres.sql.gz"
echo "Postgres dump: $(du -h "$DEST/postgres.sql.gz" | cut -f1)"

rclone copy "$MINIO_RCLONE_REMOTE" "$DEST/minio-objects/" --quiet
echo "MinIO objects: $(find "$DEST/minio-objects" -type f | wc -l | tr -d ' ') files, $(du -sh "$DEST/minio-objects" 2>/dev/null | cut -f1)"

# Prune anything older than the most recent $KEEP timestamped folders.
cd "$BACKUP_ROOT"
ls -1d [0-9]*-[0-9]* 2>/dev/null | sort -r | tail -n +$((KEEP + 1)) | while read -r old; do
    echo "Pruning old backup: $old"
    rm -rf "$old"
done

echo "── $(date) : backup complete"
