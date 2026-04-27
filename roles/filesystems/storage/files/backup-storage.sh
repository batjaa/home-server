#!/usr/bin/env bash
#
# backup-storage — daily rsync of /mnt/storage to /mnt/backup_pool plus
# per-drive btrfs read-only snapshots for accidental-delete recovery.
#
# Triggered daily by backup-storage.timer; safe to also run manually.
#
# Per-drive flow:
#   1. Take a btrfs read-only snapshot of @current into
#      /mnt/backupN/.snapshots/<YYYY-MM-DD>. Because .snapshots/ lives
#      at the fs root (subvolid=5) and NOT inside @current, we mount the
#      fs root at a temp dir to create the snapshot, then unmount.
#   2. After all snapshots are in place, rsync /mnt/storage/ to
#      /mnt/backup_pool/ (the mergerfs union over the backup drives).
#   3. If rsync succeeded, prune snapshots older than retain-days.
#      If rsync failed, leave snapshots alone so the most recent good
#      copy stays available.

set -euo pipefail

SOURCE_DIR=/mnt/storage
DEST_DIR=/mnt/backup_pool
DOCKER_DATA_SRC=/opt/docker/data
DOCKER_DATA_DEST="${DEST_DIR}/_docker_data"
LOCKFILE=/var/run/backup-storage.lock
LOGFILE=/var/log/backup-storage.log
RETAIN_DAYS="${SNAPSHOT_RETAIN_DAYS:-14}"
CHOWN_SPEC="${BACKUP_CHOWN:-1001:1001}"
RSYNC_EXTRA_OPTS="${RSYNC_EXTRA_OPTS:-}"

# Drives to snapshot. Format: "<mount>:<uuid>" pairs separated by spaces.
# Injected by the systemd unit from Ansible host_vars.
BACKUP_DRIVES="${BACKUP_DRIVES:-}"

TODAY="$(date '+%F')"
TMP_ROOT=""
declare -a TEMP_MOUNTS=()

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOGFILE" >&2; }

cleanup() {
  # Best-effort unmount any temp fs-root mounts we made
  local mp
  for mp in "${TEMP_MOUNTS[@]:-}"; do
    [[ -z "$mp" ]] && continue
    if mountpoint -q "$mp"; then
      umount "$mp" 2>/dev/null || true
    fi
    rmdir "$mp" 2>/dev/null || true
  done
  [[ -n "$TMP_ROOT" && -d "$TMP_ROOT" ]] && rmdir "$TMP_ROOT" 2>/dev/null || true
}
trap cleanup EXIT

# Single-instance lock
exec 200>"$LOCKFILE"
flock -n 200 || { log "another backup already running, exiting"; exit 0; }

if [[ -z "$BACKUP_DRIVES" ]]; then
  log "ERROR: BACKUP_DRIVES env var is empty; refusing to run"
  exit 2
fi

log "starting (source=${SOURCE_DIR}, dest=${DEST_DIR}, retain=${RETAIN_DAYS}d)"

TMP_ROOT="$(mktemp -d -t backup-storage.XXXXXX)"

# ── 1. Snapshot every backup drive ───────────────────────────────────────
for entry in $BACKUP_DRIVES; do
  mount_path="${entry%%:*}"
  uuid="${entry##*:}"
  name="$(basename "$mount_path")"

  if [[ -z "$mount_path" || -z "$uuid" || "$mount_path" == "$uuid" ]]; then
    log "ERROR: malformed BACKUP_DRIVES entry '$entry'"
    exit 2
  fi

  if ! mountpoint -q "$mount_path"; then
    log "ERROR: ${mount_path} is not mounted; aborting"
    exit 2
  fi

  fsroot="${TMP_ROOT}/${name}"
  mkdir -p "$fsroot"
  TEMP_MOUNTS+=("$fsroot")

  log "[${name}] mounting fs root (subvolid=5) at ${fsroot}"
  mount -o subvolid=5 "UUID=${uuid}" "$fsroot"

  mkdir -p "${fsroot}/.snapshots"

  snap_path="${fsroot}/.snapshots/${TODAY}"
  if [[ -e "$snap_path" ]]; then
    log "[${name}] snapshot ${TODAY} already exists; skipping"
  else
    log "[${name}] creating read-only snapshot ${snap_path}"
    btrfs subvolume snapshot -r "${fsroot}/@current" "$snap_path" >>"$LOGFILE" 2>&1
  fi

  # Unmount the fs root now that the snapshot is in place — the live
  # @current is still mounted at $mount_path for rsync to write into.
  umount "$fsroot"
  rmdir "$fsroot" 2>/dev/null || true
done

# ── 2a. rsync /mnt/storage/ → /mnt/backup_pool/ ──────────────────────────
log "rsync ${SOURCE_DIR}/ -> ${DEST_DIR}/ (chown=${CHOWN_SPEC})"
rsync_status=0
# shellcheck disable=SC2086
rsync -aHAXE --delete --info=stats2 \
  --chown="$CHOWN_SPEC" \
  $RSYNC_EXTRA_OPTS \
  "${SOURCE_DIR}/" "${DEST_DIR}/" \
  >>"$LOGFILE" 2>&1 || rsync_status=$?

# rsync exit 24 = "some files vanished" — normal when backing up live services
# (Nextcloud/Plex creating temp files that get deleted during the run).
# Treat as a warning and continue.
if [[ $rsync_status -eq 24 ]]; then
  log "WARN: rsync exit 24 (vanished source files) — treating as success"
  rsync_status=0
fi

if [[ $rsync_status -ne 0 ]]; then
  log "ERROR: rsync failed with exit ${rsync_status}; NOT pruning snapshots"
  exit "$rsync_status"
fi

# ── 2b. rsync /opt/docker/data/ → /mnt/backup_pool/_docker_data/ ─────────
# Captures app config + small DBs (API keys, indexers, libraries) so a
# disaster recovery is "restore + ansible-playbook" rather than clicking
# through every service's wizard.
mkdir -p "${DOCKER_DATA_DEST}"
log "rsync ${DOCKER_DATA_SRC}/ -> ${DOCKER_DATA_DEST}/"
docker_rsync_status=0
rsync -aHAXE --delete --info=stats2 \
  --exclude='*/Plug-in Support/Caches/' \
  --exclude='*/Cache/' \
  --exclude='*/transcode/' \
  --exclude='*/Logs/' \
  --exclude='*/log/' \
  --exclude='*/MediaCover/' \
  --exclude='*/cache/' \
  "${DOCKER_DATA_SRC}/" "${DOCKER_DATA_DEST}/" \
  >>"$LOGFILE" 2>&1 || docker_rsync_status=$?

if [[ $docker_rsync_status -eq 24 ]]; then
  log "WARN: docker_data rsync exit 24 (vanished files) — treating as success"
  docker_rsync_status=0
fi

if [[ $docker_rsync_status -ne 0 ]]; then
  log "ERROR: docker_data rsync failed with exit ${docker_rsync_status}; NOT pruning snapshots"
  exit "$docker_rsync_status"
fi

# ── 3. Prune snapshots older than RETAIN_DAYS on each backup drive ───────
log "pruning snapshots older than ${RETAIN_DAYS} days"
for entry in $BACKUP_DRIVES; do
  mount_path="${entry%%:*}"
  uuid="${entry##*:}"
  name="$(basename "$mount_path")"

  fsroot="${TMP_ROOT}/${name}"
  mkdir -p "$fsroot"
  TEMP_MOUNTS+=("$fsroot")

  mount -o subvolid=5 "UUID=${uuid}" "$fsroot"

  snap_dir="${fsroot}/.snapshots"
  if [[ -d "$snap_dir" ]]; then
    # Snapshots are named YYYY-MM-DD. Anything older than RETAIN_DAYS gets deleted.
    cutoff="$(date -d "${RETAIN_DAYS} days ago" '+%F')"
    while IFS= read -r -d '' snap; do
      sname="$(basename "$snap")"
      # Skip non-date-named entries defensively
      [[ "$sname" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || continue
      if [[ "$sname" < "$cutoff" ]]; then
        log "[${name}] deleting old snapshot ${sname}"
        btrfs subvolume delete "$snap" >>"$LOGFILE" 2>&1 || \
          log "[${name}] WARN: failed to delete ${sname}"
      fi
    done < <(find "$snap_dir" -mindepth 1 -maxdepth 1 -type d -print0)
  fi

  umount "$fsroot"
  rmdir "$fsroot" 2>/dev/null || true
done

log "finished"
