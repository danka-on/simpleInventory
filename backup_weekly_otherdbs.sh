#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_DIR="/opt/sweetshelves"
USB_MOUNT="/media/dk/USB"
BACKUP_DIR="${USB_MOUNT}/sweetshelves-db-backups/weekly"
KEEP=4
LOCK_FILE="/tmp/sweetshelves-weekly-backup.lock"
CURRENT_WORK_DIR=""
CURRENT_PARTIAL=""

cleanup_current() {
  if [[ -n "$CURRENT_WORK_DIR" ]]; then
    rm -rf -- "$CURRENT_WORK_DIR"
  fi
  if [[ -n "$CURRENT_PARTIAL" ]]; then
    rm -f -- "$CURRENT_PARTIAL"
  fi
}
finish() {
  rc=$?
  trap - EXIT
  cleanup_current
  if ((rc != 0)); then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [weekly] ERROR: backup command failed (exit=${rc})"
  fi
  exit "$rc"
}
trap finish EXIT

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [weekly] another weekly backup is already running"
  exit 0
fi

mountpoint -q "$USB_MOUNT" || {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [weekly] ERROR: USB is not mounted at ${USB_MOUNT}"
  exit 1
}
test -w "$USB_MOUNT" || {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [weekly] ERROR: USB is not writable"
  exit 1
}
mkdir -p "$BACKUP_DIR"

SUCCESS_COUNT=0
for DB in "$APP_DIR"/*.db; do
  [[ -e "$DB" ]] || continue
  NAME=$(basename "$DB")
  [[ "$NAME" == "searchRack.db" ]] && continue
  STEM="${NAME%.db}"

  mapfile -t OLD_BACKUPS < <(
    find "$BACKUP_DIR" -maxdepth 1 -type f -name "${STEM}-*.sqlite3.gz" \
      -printf '%T@ %p\n' | sort -nr | awk -v keep="$((KEEP - 1))" 'NR > keep {$1=""; sub(/^ /, ""); print}'
  )
  if ((${#OLD_BACKUPS[@]})); then
    rm -f -- "${OLD_BACKUPS[@]}"
  fi

  DB_KB=$(du -k "$DB" | awk '{print $1}')
  FREE_KB=$(df -Pk "$USB_MOUNT" | awk 'NR==2 {print $4}')
  # The uncompressed SQLite snapshot lives in /tmp, so USB only needs room
  # for the gzip output plus a safety margin.
  REQUIRED_KB=$((DB_KB / 2))
  if ((REQUIRED_KB < 5120)); then REQUIRED_KB=5120; fi
  if ((FREE_KB < REQUIRED_KB)); then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [weekly] ERROR: insufficient USB space for ${NAME} (${FREE_KB} KB free; ${REQUIRED_KB} KB required)"
    exit 1
  fi

  STAMP=$(date +%Y%m%d_%H%M%S)
  OUT="${BACKUP_DIR}/${STEM}-${STAMP}.sqlite3.gz"
  PARTIAL="${OUT}.partial"
  WORK_DIR=$(mktemp -d /tmp/sweetshelves-weekly.XXXXXX)
  TMP_DB="${WORK_DIR}/${STEM}.sqlite3"
  CURRENT_WORK_DIR="$WORK_DIR"
  CURRENT_PARTIAL="$PARTIAL"

  if sqlite3 "$DB" ".backup '$TMP_DB'" \
      && sqlite3 "$TMP_DB" "PRAGMA integrity_check;" | grep -qx "ok" \
      && gzip -c "$TMP_DB" > "$PARTIAL" \
      && gzip -t "$PARTIAL"; then
    mv -f "$PARTIAL" "$OUT"
  else
    rm -rf -- "$WORK_DIR"
    rm -f -- "$PARTIAL"
    echo "$(date '+%Y-%m-%d %H:%M:%S') [weekly] ERROR: backup failed for ${NAME}"
    exit 1
  fi
  rm -rf -- "$WORK_DIR"
  CURRENT_WORK_DIR=""
  CURRENT_PARTIAL=""

  SIZE=$(du -h "$OUT" | awk '{print $1}')
  COUNT=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "${STEM}-*.sqlite3.gz" | wc -l)
  echo "$(date '+%Y-%m-%d %H:%M:%S') [weekly] ${NAME} backup ok: $(basename "$OUT") size=${SIZE} retained=${COUNT}"
  SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
done

echo "$(date '+%Y-%m-%d %H:%M:%S') [weekly] completed: databases=${SUCCESS_COUNT} dir=${BACKUP_DIR}"
