#!/usr/bin/env bash
#
# verify-readiness.sh — Pre-flight checks for the storage Ansible role
#
# Read-only. Exits non-zero if any check fails. Run this manually before
# `ansible-playbook` to catch problems before any destructive op.
#
# Usage:  sudo ./verify-readiness.sh

set -uo pipefail

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
NC=$'\033[0m'

# ─── Drive inventory (keep in sync with host_vars/andromon/vars.yml) ──────
declare -A BYID MODEL SIZE_GB UUID

BYID[primary]="ata-ST24000NM000H-3KS103_ZYD0F3TM"
MODEL[primary]="ST24000NM000H"
SIZE_GB[primary]="24000"
UUID[primary]="29f42d78-7970-430d-ad01-fb51307dd75b"

BYID[backup1]="ata-WDC_WD40EFRX-68N32N0_WD-WCC7K1XHVJPX"
MODEL[backup1]="WD40EFRX"
SIZE_GB[backup1]="4000"
UUID[backup1]=""

BYID[backup2]="ata-WDC_WD40EFRX-68N32N0_WD-WCC7K7UTX0J4"
MODEL[backup2]="WD40EFRX"
SIZE_GB[backup2]="4000"
UUID[backup2]=""

BYID[backup3]="ata-ST4000DM005-2DP166_ZGY0JAYX"
MODEL[backup3]="ST4000DM005"
SIZE_GB[backup3]="4000"
UUID[backup3]=""

BYID[root]="nvme-Samsung_SSD_970_EVO_Plus_1TB_S6S1NS0T804851P"
MODEL[root]="970_EVO"
SIZE_GB[root]="1000"
UUID[root]="6e0041a2-881c-4b1f-b306-2264f02414e0"

ORDER=(primary backup1 backup2 backup3 root)

# ─── Counters ──────────────────────────────────────────────────────────────
PASS=0
FAIL=0
WARN=0

ok()    { printf "  ${GREEN}✓${NC} %s\n" "$1";   PASS=$((PASS+1)); }
fail()  { printf "  ${RED}✗${NC} %s\n" "$1";     FAIL=$((FAIL+1)); }
warn()  { printf "  ${YELLOW}!${NC} %s\n" "$1";  WARN=$((WARN+1)); }
section() { printf "\n${BLUE}== %s ==${NC}\n" "$1"; }

# ─── 1. Block devices present ──────────────────────────────────────────────
section "1. Block devices present"
for key in "${ORDER[@]}"; do
  path="/dev/disk/by-id/${BYID[$key]}"
  if [[ -b "$path" ]]; then
    ok "$key → $(readlink -f "$path")"
  else
    fail "$key — expected $path, not found"
  fi
done

# ─── 2. Sizes ──────────────────────────────────────────────────────────────
section "2. Drive sizes match inventory (±1%)"
for key in "${ORDER[@]}"; do
  path="/dev/disk/by-id/${BYID[$key]}"
  [[ -b "$path" ]] || continue
  bytes=$(blockdev --getsize64 "$path" 2>/dev/null || echo 0)
  gb=$((bytes / 1000 / 1000 / 1000))
  expected="${SIZE_GB[$key]}"
  diff=$(( gb > expected ? gb - expected : expected - gb ))
  pct=$(( expected > 0 ? diff * 100 / expected : 100 ))
  if (( pct <= 1 )); then
    ok "$key — ${gb}GB (expected ${expected}GB)"
  else
    fail "$key — ${gb}GB, expected ${expected}GB (${pct}% off)"
  fi
done

# ─── 3. Models ─────────────────────────────────────────────────────────────
section "3. Drive models match inventory"
for key in "${ORDER[@]}"; do
  byid="${BYID[$key]}"
  expected="${MODEL[$key]}"
  if [[ "$byid" == *"$expected"* ]]; then
    ok "$key — by-id contains '$expected'"
  else
    fail "$key — by-id $byid doesn't contain expected model '$expected'"
  fi
done

# ─── 4. Filesystem UUIDs ───────────────────────────────────────────────────
section "4. Filesystem state"
for key in "${ORDER[@]}"; do
  path="/dev/disk/by-id/${BYID[$key]}"
  [[ -b "$path" ]] || continue
  expected="${UUID[$key]}"

  if [[ -z "$expected" ]]; then
    # Drive will be wiped — surface any existing signature
    sigs=$(wipefs -n "$path" 2>/dev/null | wc -l)
    if (( sigs == 0 )); then
      ok "$key — no filesystem signature (ready to format)"
    else
      warn "$key — has existing filesystem signature (will be WIPED on first run):"
      wipefs -n "$path" 2>/dev/null | sed 's/^/      /'
    fi
  else
    # Expected UUID — scan all partitions of this device, match anywhere
    if lsblk -no UUID "$path" 2>/dev/null | grep -qx "$expected"; then
      ok "$key — UUID matches"
    else
      found=$(lsblk -no UUID "$path" 2>/dev/null | tr '\n' ' ' | xargs)
      fail "$key — UUID $expected not found.  device has: ${found:-<none>}"
    fi
  fi
done

# ─── 5. Root NVMe free space ───────────────────────────────────────────────
section "5. Root NVMe free space"
free_gb=$(df -BG / | awk 'NR==2 {gsub("G",""); print $4}')
if (( free_gb >= 250 )); then
  ok "/ has ${free_gb}GB free (≥250GB needed for cache + headroom)"
else
  fail "/ has only ${free_gb}GB free (need ≥250GB)"
fi

# ─── 6. Active processes that could conflict ───────────────────────────────
section "6. No conflicting writes in flight"
for proc in rsync dd btrfs-balance btrfs-scrub btrfs-send; do
  if pgrep -x "$proc" >/dev/null 2>&1; then
    fail "$proc is currently running — wait for it to finish"
  else
    ok "no $proc running"
  fi
done

# ─── 7. /mnt/storage usage ─────────────────────────────────────────────────
section "7. /mnt/storage open files"
if mountpoint -q /mnt/storage 2>/dev/null; then
  open=$(lsof /mnt/storage 2>/dev/null | tail -n +2 | wc -l)
  if (( open == 0 )); then
    ok "/mnt/storage mounted, no open files"
  else
    fail "/mnt/storage has $open open files — stop services using it first"
    lsof /mnt/storage 2>/dev/null | tail -n +2 | head -5 | sed 's/^/      /'
  fi
else
  ok "/mnt/storage not mounted (fresh setup state)"
fi

# ─── 8. SMART health ───────────────────────────────────────────────────────
section "8. SMART health"
if ! command -v smartctl >/dev/null 2>&1; then
  warn "smartctl not installed — skipping SMART checks (apt install smartmontools)"
else
  for key in "${ORDER[@]}"; do
    path="/dev/disk/by-id/${BYID[$key]}"
    [[ -b "$path" ]] || continue
    result=$(smartctl -H "$path" 2>/dev/null \
      | grep -E "test result|SMART overall" \
      | head -1 \
      | awk -F: '{print $2}' \
      | xargs || true)
    if [[ "$result" == "PASSED" ]]; then
      ok "$key — SMART PASSED"
    elif [[ -z "$result" ]]; then
      warn "$key — SMART status unreadable"
    else
      fail "$key — SMART: $result"
    fi
  done
fi

# ─── 9. Required tools ─────────────────────────────────────────────────────
section "9. Required tools"
for tool in lsblk blockdev wipefs btrfs rsync mergerfs; do
  if command -v "$tool" >/dev/null 2>&1; then
    if [[ "$tool" == "mergerfs" ]]; then
      ver=$(mergerfs --version 2>&1 | head -1 | awk '{print $NF}')
      ok "mergerfs installed (version $ver)"
    else
      ok "$tool installed"
    fi
  else
    fail "$tool missing"
  fi
done

if [[ -f /etc/fuse.conf ]]; then
  ok "/etc/fuse.conf exists"
else
  fail "/etc/fuse.conf missing"
fi

# ─── Summary ───────────────────────────────────────────────────────────────
echo
printf "═══════════════════════════════════════\n"
printf "  ${GREEN}PASS:%4d${NC}   ${RED}FAIL:%4d${NC}   ${YELLOW}WARN:%4d${NC}\n" "$PASS" "$FAIL" "$WARN"
printf "═══════════════════════════════════════\n"

if (( FAIL > 0 )); then
  echo
  echo "Some checks failed. Resolve the issues above before running the playbook."
  exit 1
fi

if (( WARN > 0 )); then
  echo
  echo "All required checks passed, but warnings need your attention."
  exit 0
fi

echo
echo "All checks passed. Safe to run the storage playbook."
exit 0
