#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# HoneyBadger Sentinel - Database Backup Script
# Creates timestamped backups of the SQLite database
#
# Usage:
#   ./backup-db.sh                    # Manual backup
#   ./backup-db.sh --cron             # For cron jobs (quiet mode)
#
# Cron example (daily at 2am):
#   0 2 * * * /opt/hbv-sentinel/scripts/backup-db.sh --cron
# ═══════════════════════════════════════════════════════════════════════

set -e

# Configuration
DB_PATH="${HBV_DB_PATH:-/opt/hbv-sentinel/sentinel.db}"
BACKUP_DIR="${HBV_BACKUP_DIR:-/opt/hbv-sentinel/backups}"
RETENTION_DAYS="${HBV_BACKUP_RETENTION:-7}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="sentinel_backup_${TIMESTAMP}.db"
QUIET_MODE=false

# Parse arguments
if [ "$1" = "--cron" ]; then
    QUIET_MODE=true
fi

log() {
    if [ "$QUIET_MODE" = false ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    fi
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $1" >&2
}

# Check if database exists
if [ ! -f "$DB_PATH" ]; then
    error "Database not found at $DB_PATH"
    exit 1
fi

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

log "Starting backup of $DB_PATH"

# Use SQLite's backup command for consistency
# This creates a consistent snapshot even if the database is being written to
sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/$BACKUP_FILE'"

if [ $? -eq 0 ]; then
    # Compress the backup
    gzip "$BACKUP_DIR/$BACKUP_FILE"
    BACKUP_SIZE=$(du -h "$BACKUP_DIR/${BACKUP_FILE}.gz" | cut -f1)
    log "Backup created: ${BACKUP_FILE}.gz ($BACKUP_SIZE)"
else
    error "Backup failed!"
    exit 1
fi

# Cleanup old backups
log "Cleaning up backups older than $RETENTION_DAYS days"
find "$BACKUP_DIR" -name "sentinel_backup_*.db.gz" -mtime +$RETENTION_DAYS -delete 2>/dev/null || true

# Count remaining backups
BACKUP_COUNT=$(find "$BACKUP_DIR" -name "sentinel_backup_*.db.gz" | wc -l)
log "Backup complete. Total backups: $BACKUP_COUNT"

# Verify backup integrity
log "Verifying backup integrity..."
gunzip -c "$BACKUP_DIR/${BACKUP_FILE}.gz" > /tmp/sentinel_verify_$$.db
INTEGRITY=$(sqlite3 /tmp/sentinel_verify_$$.db "PRAGMA integrity_check;" 2>/dev/null)
rm -f /tmp/sentinel_verify_$$.db

if [ "$INTEGRITY" = "ok" ]; then
    log "Backup integrity verified: OK"
else
    error "Backup integrity check failed!"
    exit 1
fi

log "Backup completed successfully"
