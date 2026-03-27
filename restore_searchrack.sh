#!/usr/bin/env bash
set -euo pipefail

DEFAULT_BACKUP_DIR="/mnt/db-backups/sweetshelves-db-backups/daily"
BACKUP_FILE="${1:-}"

if [ -z "$BACKUP_FILE" ]; then
  BACKUP_FILE="$(ls -t "${DEFAULT_BACKUP_DIR}"/searchRack-*.sqlite3.gz 2>/dev/null | head -1 || true)"
fi

if [ -z "$BACKUP_FILE" ]; then
  echo "No backup file found."
  echo "Usage: $0 [optional: /path/to/searchRack-YYYYMMDD_HHMMSS.sqlite3.gz]"
  exit 1
fi

APP_DIR="/opt/sweetshelves"
TARGET_DB="${APP_DIR}/searchRack.db"
TMP_DB="/tmp/searchRack_restore_$$.sqlite3"
STAMP=$(date +%Y%m%d_%H%M%S)
SAFETY_DB="${APP_DIR}/searchRack.db.before-restore-${STAMP}"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "Backup file not found: $BACKUP_FILE"
  exit 1
fi

echo "Stopping sweetshelves.service..."
sudo systemctl stop sweetshelves.service

cleanup() {
  rm -f "$TMP_DB"
}
trap cleanup EXIT

echo "Creating safety copy: $SAFETY_DB"
cp "$TARGET_DB" "$SAFETY_DB"

echo "Decompressing backup..."
gunzip -c "$BACKUP_FILE" > "$TMP_DB"

echo "Running integrity check..."
if ! sqlite3 "$TMP_DB" "PRAGMA integrity_check;" | grep -qx "ok"; then
  echo "Integrity check failed. Original database left untouched."
  exit 1
fi

echo "Restoring database..."
mv "$TMP_DB" "$TARGET_DB"

echo "Starting sweetshelves.service..."
sudo systemctl start sweetshelves.service

echo "Restore complete."
echo "Restored from: $BACKUP_FILE"
echo "Safety copy:    $SAFETY_DB"
