#!/usr/bin/env bash
set -euo pipefail

USB_MOUNT="/media/dk/USB"
DEFAULT_BACKUP_DIR="${USB_MOUNT}/sweetshelves-db-backups/daily"
BACKUP_FILE="${1:-}"

mountpoint -q "$USB_MOUNT" || {
  echo "USB backup drive is not mounted at ${USB_MOUNT}."
  exit 1
}

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
SERVICE_STOPPED=0

if [ ! -f "$BACKUP_FILE" ]; then
  echo "Backup file not found: $BACKUP_FILE"
  exit 1
fi

cleanup() {
  rm -f "$TMP_DB"
  if [ "$SERVICE_STOPPED" -eq 1 ]; then
    echo "Recovery: restarting sweetshelves.service..."
    sudo systemctl start sweetshelves.service || true
  fi
}
trap cleanup EXIT

echo "Decompressing backup..."
gunzip -c "$BACKUP_FILE" > "$TMP_DB"

echo "Running integrity check..."
if ! sqlite3 "$TMP_DB" "PRAGMA integrity_check;" | grep -qx "ok"; then
  echo "Integrity check failed. Original database left untouched."
    exit 1
fi

echo "Stopping sweetshelves.service..."
sudo systemctl stop sweetshelves.service
SERVICE_STOPPED=1

echo "Creating safety copy: $SAFETY_DB"
cp "$TARGET_DB" "$SAFETY_DB"

echo "Restoring database..."
mv "$TMP_DB" "$TARGET_DB"

echo "Starting sweetshelves.service..."
sudo systemctl start sweetshelves.service
SERVICE_STOPPED=0

echo "Restore complete."
echo "Restored from: $BACKUP_FILE"
echo "Safety copy:    $SAFETY_DB"
