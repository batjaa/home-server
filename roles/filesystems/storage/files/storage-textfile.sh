#!/usr/bin/env bash
#
# storage-textfile — emit Prometheus metrics about storage health.
# Run hourly by storage-textfile.timer; node_exporter's textfile collector
# scrapes the resulting .prom file.

set -uo pipefail

OUT_DIR=/var/lib/node_exporter/textfile_collector
OUT_FILE="$OUT_DIR/storage.prom"
TMP=$(mktemp "$OUT_DIR/.storage.prom.XXXXXX")

# Always clean up the tmp file on exit
trap 'rm -f "$TMP"' EXIT

emit() { printf '%s\n' "$@" >> "$TMP"; }

# ─── Header ────────────────────────────────────────────────────────────────
emit "# HELP smart_health_ok 1 if SMART overall-health PASSED, 0 otherwise."
emit "# TYPE smart_health_ok gauge"
emit "# HELP smart_temperature_celsius Drive temperature reported by SMART."
emit "# TYPE smart_temperature_celsius gauge"
emit "# HELP smart_power_on_hours Power-on hours from SMART attributes."
emit "# TYPE smart_power_on_hours gauge"
emit "# HELP btrfs_scrub_last_finish_seconds Unix timestamp of last completed scrub."
emit "# TYPE btrfs_scrub_last_finish_seconds gauge"
emit "# HELP btrfs_scrub_errors_total Errors found in last completed scrub."
emit "# TYPE btrfs_scrub_errors_total gauge"
emit "# HELP storage_backup_last_run_seconds Unix timestamp of last backup-storage run."
emit "# TYPE storage_backup_last_run_seconds gauge"
emit "# HELP storage_backup_last_run_success 1 if last backup-storage run succeeded."
emit "# TYPE storage_backup_last_run_success gauge"
emit "# HELP storage_mover_last_run_seconds Unix timestamp of last cache-mover run."
emit "# TYPE storage_mover_last_run_seconds gauge"

# ─── SMART per drive ───────────────────────────────────────────────────────
for path in /dev/disk/by-id/ata-* /dev/disk/by-id/nvme-*; do
  [[ -L "$path" ]] || continue
  # Skip partition aliases — only emit metrics for whole-disk by-id symlinks
  case "$(basename "$path")" in
    *-part[0-9]*) continue ;;
  esac
  dev=$(readlink -f "$path")
  label=$(basename "$path")

  json=$(smartctl -A -H -i -j "$dev" 2>/dev/null) || continue
  [[ -n "$json" ]] || continue

  passed=$(echo "$json" | jq -r '.smart_status.passed // empty' 2>/dev/null)
  if [[ "$passed" == "true" ]]; then
    emit "smart_health_ok{drive=\"$label\"} 1"
  elif [[ "$passed" == "false" ]]; then
    emit "smart_health_ok{drive=\"$label\"} 0"
  fi

  temp=$(echo "$json" | jq -r '.temperature.current // empty' 2>/dev/null)
  [[ -n "$temp" && "$temp" != "null" ]] && emit "smart_temperature_celsius{drive=\"$label\"} $temp"

  hours=$(echo "$json" | jq -r '.power_on_time.hours // empty' 2>/dev/null)
  [[ -n "$hours" && "$hours" != "null" ]] && emit "smart_power_on_hours{drive=\"$label\"} $hours"
done

# ─── btrfs scrub status ────────────────────────────────────────────────────
for mp in /mnt/sda1 /mnt/backup1 /mnt/backup2 /mnt/backup3; do
  [[ -d "$mp" ]] && mountpoint -q "$mp" || continue

  status=$(btrfs scrub status -R "$mp" 2>/dev/null) || continue
  [[ -n "$status" ]] || continue

  # ended timestamp (epoch). Format: "Scrub started/ended: <date>"
  ended=$(echo "$status" | awk -F': *' '/Scrub.*finished:/ {print $2; exit}')
  if [[ -n "$ended" ]]; then
    epoch=$(date -d "$ended" +%s 2>/dev/null || true)
    [[ -n "$epoch" ]] && emit "btrfs_scrub_last_finish_seconds{mount=\"$mp\"} $epoch"
  fi

  errors=$(echo "$status" | awk '/error_count/ {print $NF; exit}')
  [[ -n "$errors" ]] && emit "btrfs_scrub_errors_total{mount=\"$mp\"} $errors"
done

# ─── Cache mover last run ──────────────────────────────────────────────────
last_mover=$(systemctl show cache-mover.service -p ExecMainExitTimestampMonotonic 2>/dev/null | cut -d= -f2)
if [[ -n "$last_mover" && "$last_mover" != "0" ]]; then
  # Convert monotonic offset to wall-clock epoch (approximate via service timestamp)
  exec_main_exit=$(systemctl show cache-mover.service -p ExecMainExitTimestamp --value 2>/dev/null)
  if [[ -n "$exec_main_exit" ]]; then
    epoch=$(date -d "$exec_main_exit" +%s 2>/dev/null || true)
    [[ -n "$epoch" ]] && emit "storage_mover_last_run_seconds $epoch"
  fi
fi

# ─── Backup last run + success ─────────────────────────────────────────────
backup_exec_main_exit=$(systemctl show backup-storage.service -p ExecMainExitTimestamp --value 2>/dev/null)
if [[ -n "$backup_exec_main_exit" ]]; then
  epoch=$(date -d "$backup_exec_main_exit" +%s 2>/dev/null || true)
  [[ -n "$epoch" ]] && emit "storage_backup_last_run_seconds $epoch"
fi

backup_status=$(systemctl show backup-storage.service -p ExecMainStatus --value 2>/dev/null)
if [[ -n "$backup_status" ]]; then
  if [[ "$backup_status" == "0" ]]; then
    emit "storage_backup_last_run_success 1"
  else
    emit "storage_backup_last_run_success 0"
  fi
fi

# ─── Atomic publish ────────────────────────────────────────────────────────
chmod 0644 "$TMP"
mv "$TMP" "$OUT_FILE"
trap - EXIT
