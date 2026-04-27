#!/usr/bin/env bash
#
# cache-mover — move stale files from NVMe cache to primary disk.
# Triggered daily by cache-mover.timer; safe to also run manually.

set -euo pipefail

CACHE_DIR=/mnt/storage_cache
TARGET_DIR=/mnt/sda1
AGE_DAYS="${CACHE_AGE_DAYS:-7}"
LOCKFILE=/var/run/cache-mover.lock
LOGFILE=/var/log/cache-mover.log

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOGFILE" >&2; }

# Single-instance lock
exec 200>"$LOCKFILE"
flock -n 200 || { log "another mover already running, exiting"; exit 0; }

log "starting (atime > ${AGE_DAYS} days, ${CACHE_DIR} -> ${TARGET_DIR})"

LIST=$(mktemp)
trap 'rm -f "$LIST"' EXIT

cd "$CACHE_DIR"
find . -type f -atime +"$AGE_DAYS" -printf '%P\n' > "$LIST"

count=$(wc -l < "$LIST")
log "$count file(s) to move"

if [[ $count -gt 0 ]]; then
  rsync -axqHAXWE --preallocate --remove-source-files \
    --files-from="$LIST" \
    "$CACHE_DIR/" "$TARGET_DIR/" \
    >>"$LOGFILE" 2>&1
fi

# Clean up empty subdirs (preserves the cache root)
find "$CACHE_DIR" -mindepth 1 -type d -empty -delete

log "finished"
