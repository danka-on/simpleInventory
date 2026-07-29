#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_DIR="/opt/sweetshelves"
USB_MOUNT="/media/dk/USB"
BACKUP_DIR="${USB_MOUNT}/sweetshelves-db-backups/daily"
DB_PATH="${APP_DIR}/searchRack.db"
KEEP=14
LOCK_FILE="/tmp/sweetshelves-daily-backup.lock"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [daily] another backup is already running"
  exit 0
fi

mountpoint -q "$USB_MOUNT" || {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [daily] ERROR: USB is not mounted at ${USB_MOUNT}"
  exit 1
}
test -w "$USB_MOUNT" || {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [daily] ERROR: USB is not writable"
  exit 1
}
test -f "$DB_PATH" || {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [daily] ERROR: database not found: ${DB_PATH}"
  exit 1
}

mkdir -p "$BACKUP_DIR"

# Make room before writing while retaining KEEP-1 known-good generations.
mapfile -t OLD_BACKUPS < <(
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'searchRack-*.sqlite3.gz' \
    -printf '%T@ %p\n' | sort -nr | awk -v keep="$((KEEP - 1))" 'NR > keep {$1=""; sub(/^ /, ""); print}'
)
if ((${#OLD_BACKUPS[@]})); then
  rm -f -- "${OLD_BACKUPS[@]}"
fi

DB_KB=$(du -k "$DB_PATH" | awk '{print $1}')
FREE_KB=$(df -Pk "$USB_MOUNT" | awk 'NR==2 {print $4}')
REQUIRED_KB=$((DB_KB * 2))
if ((REQUIRED_KB < 10240)); then REQUIRED_KB=10240; fi
if ((FREE_KB < REQUIRED_KB)); then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [daily] ERROR: insufficient USB space (${FREE_KB} KB free; ${REQUIRED_KB} KB required)"
  exit 1
fi

STAMP=$(date +%Y%m%d_%H%M%S)
OUT="${BACKUP_DIR}/searchRack-${STAMP}.sqlite3.gz"
PARTIAL="${OUT}.partial"
WORK_DIR=$(mktemp -d /tmp/sweetshelves-daily.XXXXXX)
TMP_DB="${WORK_DIR}/searchRack.sqlite3"
cleanup() {
  rm -rf -- "$WORK_DIR"
  rm -f -- "$PARTIAL"
}
finish() {
  rc=$?
  trap - EXIT
  cleanup
  if ((rc != 0)); then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [daily] ERROR: backup command failed (exit=${rc})"
  fi
  exit "$rc"
}
trap finish EXIT

sqlite3 "$DB_PATH" ".backup '$TMP_DB'"
sqlite3 "$TMP_DB" "PRAGMA integrity_check;" | grep -qx "ok"
gzip -c "$TMP_DB" > "$PARTIAL"
gzip -t "$PARTIAL"
mv -f "$PARTIAL" "$OUT"

SIZE=$(du -h "$OUT" | awk '{print $1}')
COUNT=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'searchRack-*.sqlite3.gz' | wc -l)
echo "$(date '+%Y-%m-%d %H:%M:%S') [daily] searchRack backup ok: $(basename "$OUT") size=${SIZE} retained=${COUNT} dir=${BACKUP_DIR}"
