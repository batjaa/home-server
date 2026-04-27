# Storage Ansible Role — Requirements

Goal: codify the home-server storage layout into an idempotent Ansible role so the configuration survives reinstalls, drive shuffles, and partial reboots — and so future changes (snapraid when a 2nd 24T drive lands, drive upgrades) are diff-able.

This document is the **handoff brief** to a separate session that will write the role. Treat the host's current state as the source of truth for what already works; the role should reproduce it, not invent a new design.

---

## Target topology

```
/  (NVMe, ext4, untouched)
└── /mnt/storage_cache/      directory only — no separate partition
                             mergerfs branch with minfreespace=200G

/dev/sda1  → /mnt/sda1       btrfs, label "primary", was /mnt/storage

/mnt/storage = mergerfs(/mnt/storage_cache : /mnt/sda1)
   • new writes land on cache (NVMe)
   • mover ages cache files >7d → /mnt/sda1
   • mergerfs falls through to /mnt/sda1 when NVMe has <200G free

/dev/sdd1 → /mnt/backup1     btrfs with @current subvol — wipe + reformat (data is just a backup of sda)
/dev/sde1 → /mnt/backup2     btrfs with @current subvol — wipe + reformat (data is just a backup of sda)
/dev/sdb1 → /mnt/backup3     btrfs with @current subvol — currently unformatted

/mnt/backup_pool = mergerfs(/mnt/backup1 : /mnt/backup2 : /mnt/backup3)
   • category.create=mfs (most-free-space)
   • used only as the rsync target for nightly backups
   • ~10.8T usable across 3 mixed-vendor drives (1× Seagate, 2× WD Red)
```

---

## Drive identity (lock by serial, NOT /dev/sdX)

`/dev/sdX` letters are unstable. Ansible MUST resolve drives via `/dev/disk/by-id/` and pre-flight-verify model + size + serial before any destructive op.

| Role | Model | Serial | by-id path (preferred) | Filesystem UUID |
|---|---|---|---|---|
| **Primary data (sda)** | Seagate ST24000NM000H | `ZYD0F3TM` | `/dev/disk/by-id/ata-ST24000NM000H-3KS103_ZYD0F3TM` | `29f42d78-7970-430d-ad01-fb51307dd75b` |
| **Backup #1 (sdd)** | WD Red WD40EFRX-68N32N0 | `WD-WCC7K1XHVJPX` | `/dev/disk/by-id/ata-WDC_WD40EFRX-68N32N0_WD-WCC7K1XHVJPX` | `1189a36f-7dda-4f7a-9065-1169f62a8c6e` |
| **Backup #2 (sde)** | WD Red WD40EFRX-68N32N0 | `WD-WCC7K7UTX0J4` | `/dev/disk/by-id/ata-WDC_WD40EFRX-68N32N0_WD-WCC7K7UTX0J4` | `61e9e54c-7517-41b9-8289-180241b125c9` |
| **Backup #3 (sdb)** | Seagate ST4000DM005-2DP1 | `ZGY0JAYX` | `/dev/disk/by-id/ata-ST4000DM005-2DP166_ZGY0JAYX` | *(no filesystem yet — assigned by role on first mkfs)* |
| **Boot/root NVMe** | Samsung 970 EVO Plus 1TB | `S6S1NS0T804851P` | `/dev/disk/by-id/nvme-Samsung_SSD_970_EVO_Plus_1TB_S6S1NS0T804851P` | `6e0041a2-881c-4b1f-b306-2264f02414e0` |

### Pre-flight assertion the role must run before any disk-modifying task
For each role-to-drive mapping, assert:
1. The expected `by-id` path resolves to a present block device
2. Reported size matches expected (±1%)
3. Reported model matches expected
4. **If a UUID is expected**: device's filesystem UUID must match — otherwise abort (do not reformat without an explicit `--force-wipe` flag).
5. **If no UUID is expected** (sdb on first run): device must report no existing filesystem signature (`wipefs -n` is empty). If a signature is present, abort and surface it — the disk may have been swapped or repurposed.

After a successful first format of sdb, the role must record the new UUID into a host-vars file (or generate-once and pin via `mkfs.btrfs -U <fixed-uuid>`) so subsequent runs can apply rule 4 instead of rule 5.

This protects against the failure mode of "the playbook formats the wrong drive after a controller swap."

---

## Filesystems and mounts (fstab entries the role must produce)

```
# Root and EFI (untouched)
UUID=6e0041a2-881c-4b1f-b306-2264f02414e0 /         ext4 defaults      0 1
UUID=A6AC-634B                            /boot/efi vfat defaults      0 1

# Primary data tier — sda 24T
UUID=29f42d78-7970-430d-ad01-fb51307dd75b /mnt/sda1 btrfs defaults,nofail 0 2

# Backup drives — all 3 wiped + reformatted with @current subvolume layout
# Existing data on backup1/backup2 is just a backup of sda, safe to wipe and re-seed
UUID=<assigned-on-first-format-backup1> /mnt/backup1 btrfs subvol=@current,defaults,nofail 0 2
UUID=<assigned-on-first-format-backup2> /mnt/backup2 btrfs subvol=@current,defaults,nofail 0 2
UUID=<assigned-on-first-format-backup3> /mnt/backup3 btrfs subvol=@current,defaults,nofail 0 2

# Mergerfs union — primary storage with NVMe cache tier
/mnt/storage_cache:/mnt/sda1 /mnt/storage fuse.mergerfs \
  defaults,allow_other,use_ino,category.create=ff,minfreespace=200G,moveonenospc=1,fsname=mergerfs-storage,uid=1001,gid=1001 0 0

# Mergerfs union — pooled backup target (3-drive, mixed vendor, ~10.8T)
/mnt/backup1:/mnt/backup2:/mnt/backup3 /mnt/backup_pool fuse.mergerfs \
  defaults,allow_other,use_ino,category.create=mfs,minfreespace=20G,moveonenospc=1,fsname=mergerfs-backup,uid=1001,gid=1001 0 0
```

The role must back up `/etc/fstab` to `/etc/fstab.bak-<timestamp>` before editing.

---

## Mover (cache → primary)

Service: `cache-mover.service`, timer fires daily at **02:00 local**.

Behavior:
- Move files in `/mnt/storage_cache/` whose **atime > 7 days** to `/mnt/sda1/`, preserving the relative path
- Use `rsync -axqHAXWE --preallocate --remove-source-files`
- Clean up empty directories in cache after move (`find … -type d -empty -delete`)
- Single-instance lock via `flock` on `/var/run/cache-mover.lock`
- Log to `/var/log/cache-mover.log` (logrotate-ed)
- On failure: exit non-zero (let systemd notify via `OnFailure=`)

Tunables (Ansible variables):
- `cache_age_days` (default 7)
- `mover_schedule` (default `02:00`)
- `cache_minfreespace` (default `200G`, must match mergerfs option)

---

## Backup (primary → backup_pool with btrfs snapshots)

Service: `backup-storage.service`, timer fires daily at **03:00 local** (after mover).

Behavior:
1. Acquire `flock` on `/var/run/backup-storage.lock`
2. Take read-only btrfs snapshot on **each** underlying backup drive:
   - `/mnt/backup1/.snapshots/<YYYY-MM-DD>` (subvolume snapshot of `/mnt/backup1`)
   - `/mnt/backup2/.snapshots/<YYYY-MM-DD>` (same on backup2)
   - Note: btrfs requires the snapshot source to be a subvolume; on first run, role must convert each backup root to a subvolume if it isn't already (or create a `current/` subvolume and rsync into that — pick one and document)
3. `rsync -aHAXE --delete --info=stats2 /mnt/storage/ /mnt/backup_pool/`
4. Prune snapshots older than **14 days** on both backup drives
5. Log to `/var/log/backup-storage.log`
6. Atomic-ish: if rsync fails, do NOT prune snapshots (leave the most recent good one in place)

Tunables:
- `backup_schedule` (default `03:00`)
- `snapshot_retain_days` (default 14)
- `rsync_extra_opts` (default empty; allow caller to add e.g. bandwidth limits)

### Subvolume layout — clean wipe approach

All 3 backup drives are wiped and reformatted from scratch with this layout:

```
/mnt/backup1/                    ← @current subvolume mounted here
├── (live data, written here by rsync)
└── (no .snapshots/ dir inside @current)

/mnt/backup1/.snapshots/         ← lives in fs root (subvol=5), outside @current
├── 2026-04-27/
└── 2026-04-26/
```

Setup (per drive):
1. Wipe drive: `wipefs -a /dev/sdX`
2. Create GPT + single partition
3. `mkfs.btrfs -L backup<N> /dev/sdX1`
4. Mount fs root temporarily, create `@current` subvolume: `btrfs subvolume create /mnt/tmp/@current`
5. Update fstab to mount with `subvol=@current`
6. Snapshots taken via `btrfs subvolume snapshot -r /mnt/backup<N> /mnt/backup<N>/../snapshots-<N>/<date>` — but since `.snapshots/` lives at fs root (not inside @current), need a separate temporary mount or use `subvolid=5` mount option for snapshot operations

This avoids any data migration. Existing backup data on sdd/sde is sacrificed — it will be re-populated on first backup run from sda (the canonical source).

---

## Health and observability

Should be in the role from day one (cheap to add, expensive to retrofit):

- `smartmontools` package + `smartd.conf` covering all 5 drives (sda, sdb, sdd, sde, nvme0n1) — daily short test, weekly long test
- btrfs scrub timer for `/mnt/sda1`, `/mnt/backup1`, `/mnt/backup2`, `/mnt/backup3` — monthly
- `/var/log/cache-mover.log`, `/var/log/backup-storage.log` rotated weekly, kept 8 weeks
- Optional but desirable: a tiny systemd `OnFailure=` handler that writes to a file like `/var/log/storage-failures.log` so failures are visible without external infra (we'll wire alerting later)

---

## State persistence (drift detection + DR cheat sheet)

After every successful run, the role writes `/var/lib/storage-role/state.yml`. This file is the role's record of "what I configured last time," and serves three purposes:

1. **Drift detection** on subsequent runs — role compares declared inventory vs. last-known state. If sdb's UUID in inventory changes between runs (e.g., human edited inventory), role surfaces a warning before acting.
2. **First-format UUID capture** — when the role formats sdb on first run, the chosen UUID is recorded here. Subsequent runs read this back, verifying inventory still matches what's actually on disk.
3. **Disaster-recovery cheat sheet** — if the inventory in git is ever lost, this file (when recovered from a home backup) tells you exactly what the inventory *should* contain.

### Format
```yaml
# /var/lib/storage-role/state.yml — written by the storage-role on success
schema_version: 1
last_run_utc: "2026-04-25T03:14:22Z"
ansible_role_version: "1.2.0"

drives:
  primary:
    by_id: "ata-ST24000NM000H-3KS103_ZYD0F3TM"
    fs_uuid: "29f42d78-7970-430d-ad01-fb51307dd75b"
    mount: "/mnt/sda1"
  backup1:
    by_id: "ata-WDC_WD40EFRX-68N32N0_WD-WCC7K1XHVJPX"
    fs_uuid: "1189a36f-7dda-4f7a-9065-1169f62a8c6e"
    mount: "/mnt/backup1"
  backup2:
    by_id: "ata-WDC_WD40EFRX-68N32N0_WD-WCC7K7UTX0J4"
    fs_uuid: "61e9e54c-7517-41b9-8289-180241b125c9"
    mount: "/mnt/backup2"
  backup3:
    by_id: "ata-ST4000DM005-2DP166_ZGY0JAYX"
    fs_uuid: "<assigned-on-first-format>"
    mount: "/mnt/backup3"

mergerfs:
  version: "2.33.3"
  storage_branches: ["/mnt/storage_cache", "/mnt/sda1"]
  storage_options: "category.create=ff,minfreespace=200G,..."
  backup_pool_branches: ["/mnt/backup1", "/mnt/backup2", "/mnt/backup3"]
  backup_pool_options: "category.create=mfs,minfreespace=20G,..."

fstab_sha256: "<sha256 of the storage-managed lines from /etc/fstab>"
```

### Drift handling
On each run, BEFORE making changes:
- If `state.yml` is absent: this is first run on host (fine, proceed)
- If present and inventory matches: proceed normally
- If present and inventory has different UUIDs/serials: print a diff, abort unless `--accept-drift` flag is passed (forcing the inventory to win and rewriting state)

### Backing it up
The file is small (<1KB). Role should ensure `/var/lib/storage-role/` is included in any host-level backup (rsync of `/etc`, `/var/lib`, `/home`). Consider also having the role drop a copy at `/mnt/storage/.storage-role-state.yml` after each run — that way it travels with the data drives themselves.

---

## What the role must NOT do

- Touch the NVMe partition table (no resize, no new partition — cache is just a directory on `/`)
- Reformat sdd or sde (they hold real data — assert UUIDs match before mounting)
- Touch `/dev/sdc`, `/dev/sdf`, `/dev/sdg`, `/dev/sdh` if present (these are wiped and slated for retirement; ignore them)
- Re-format sdb if it already has a btrfs filesystem matching the recorded UUID (only first run creates the FS)
- Configure snapraid (deferred until a 2nd ≥24T drive is added — out of scope here)
- Configure docker, plex, nextcloud — out of scope (other roles will consume `/mnt/storage`)

---

## Migration from current state

The host is currently in an "almost there" state:
- `/mnt/storage` is sda mounted directly (mergerfs not yet in front)
- sda has ~4T of data already laid out (`Photography`, `Music`, `Photos`, etc.) — this is the canonical data
- Backups on sdd/sde are populated from yesterday's seed run — these are **expendable**, just backup copies of sda

The role's first run will:
1. Unmount `/mnt/storage`
2. Edit fstab so sda mounts at `/mnt/sda1` instead (preserves the 4T of data)
3. Create `/mnt/storage_cache` directory on `/`
4. **Wipe + format all 3 backup drives** (sdd, sde, sdb): GPT, single partition, btrfs, with `@current` subvolume
5. Mount all 3 backup drives at `/mnt/backup{1,2,3}` via `subvol=@current`
6. Add the two mergerfs lines (storage union + backup_pool)
7. `mount -a`
8. Verify `/mnt/storage` shows the union and the existing 4T of sda data is visible
9. Verify `/mnt/backup_pool` is empty mergerfs union of 3 fresh drives
10. First backup run (one-shot, manually triggered) re-seeds backup_pool from `/mnt/storage`

The role MUST be written so that re-running it on a host that's already in the target state is a no-op (idempotent). Use UUID/by-id checks rather than mountpoint-content checks. **Once a backup drive has been formatted and its UUID recorded in state.yml, the role refuses to wipe it again unless explicitly given `--force-wipe`.**

---

## Resolved decisions

1. **Timers** — systemd timers with `OnFailure=` hooks
2. **Snapshot layout** — `@current` subvolume on all 3 backup drives (no migration; backup data is expendable, will re-seed from sda)
3. **Notifications** — local log file + Prometheus textfile collector (cheap, surfaces in Grafana later)
4. **`--delete` retention** — 14 days
5. **Service user** — role runs as root, but `rsync --chown=1001:1001` so files end up owned by `batjaa`
6. **Cache filesystem** — directory on root ext4, no separate partition

---

## Pre-flight checklist (human, before running the playbook)

The role's programmatic pre-flight (drive serials, sizes, UUIDs) is the last line of defense. These are the human steps that come *before* `ansible-playbook` is invoked. A `verify-readiness.sh` script in the role repo should mechanize as much of this as possible.

### Universal — every run, every host

- [ ] **Inventory is current**: serials in `group_vars/<host>.yml` match drives currently connected. Use `lsblk -o NAME,SIZE,MODEL,SERIAL` to compare.
- [ ] **Pinned UUIDs in inventory** for any drive that will be formatted by the role (only sdb in this setup). Generate once with `uuidgen`, commit to inventory, used via `mkfs.btrfs -U <uuid>`.
- [ ] **NVMe free space ≥ 250G** (200G cache target + 50G headroom for root growth). Check: `df -h /`.
- [ ] **No critical write in flight** — `lsof /mnt/storage` is empty (or only your shell), `pgrep rsync|cp|mv|tar|dd` is empty.
- [ ] **`/etc/fstab` is clean** (no temporary mount lines from manual debugging). Role backs it up automatically, but checking first avoids confusion.
- [ ] **smartctl is installed** and a SMART self-test on each drive is recent and clean: `for d in sda sdb sdd sde nvme0n1; do sudo smartctl -H /dev/$d | grep "test result"; done`. Don't begin a major reconfiguration with a degraded drive — restore-from-backup can't help you if the source disk is failing.
- [ ] **Live console access** (physical or IPMI/iDRAC) — if Ansible breaks the SSH path through a fstab error, you need a way back in.

### When running on the **current host** (transition from sda-direct to mergerfs)

- [ ] **`/mnt/storage` currently mounted as btrfs from `/dev/sda1`** (verify: `mount | grep storage`).
- [ ] **Expected ~4T of data on sda** (verify: `du -sh /mnt/storage/*` shows Photography 1.4T, Photos 388G, etc.).
- [ ] **Backup drives mounted and populated** — sdd1 at /mnt/backup1 (~3.6T), sde1 at /mnt/backup2 (~3.6T).
- [ ] **sdb visible to kernel and blank** (no FS signature): `lsblk /dev/sdb` shows no children, `sudo wipefs -n /dev/sdb` is empty.
- [ ] **All services that use /mnt/storage are stopped**:
  - [ ] Plex (`docker stop plex` or `systemctl stop plexmediaserver`)
  - [ ] Nextcloud (if running) — its data path under /mnt/storage means stopping the docker container *and* php-fpm/cron jobs
  - [ ] Photoprism, Jellyfin, anything else with bind mounts
  - [ ] Any backup tool (Duplicati, restic, borg) currently scheduled
  - [ ] `crontab -l` and any per-service crons under `/etc/cron.d/` — disable any that touch /mnt/storage during the maintenance window
- [ ] **Running docker containers list saved**: `docker ps > /tmp/pre-storage-reconfig-containers.txt` so you can diff after.
- [ ] **`/etc` is git-tracked or recently backed up** (etckeeper or a quick `tar czf /tmp/etc-backup.tgz /etc`).

### When running on a **fresh install / disaster recovery**

- [ ] **OS freshly installed**, ansible+python+sudo present.
- [ ] **All data drives physically connected** — confirmed by `lsblk` showing the expected serials. **No data drives missing** (the role will halt on missing serials, which is correct, but you want to discover this before the run).
- [ ] **Data drives untouched** — DO NOT run any "disk setup" wizard during OS install. The data drives must come up unformatted-from-the-OS-perspective, with their existing filesystems intact.
- [ ] **No automounts active** — some distros automount unknown filesystems. Disable udisks2 / gnome-disks / KDE auto-mount before first ansible run, or you'll fight the role.
- [ ] **Inventory deployed to the controller** with the same UUIDs/serials from the original setup. (If inventory is lost, you can recover UUIDs by mounting each drive read-only and reading them back, but this is painful — keep the inventory in git.)
- [ ] **Wipe-protection drives confirmed absent or unplugged**: sdc/sdf/sdg/sdh might have been physically retired by now; if not, the role should ignore them but you should still confirm they don't show unexpected data.

### Recommended verify-readiness.sh

A script in the role repo that runs the mechanizable parts of the above and exits non-zero if any fail. Should check:

1. All expected by-id paths resolve to block devices
2. Each block device's size matches inventory ±1%
3. Each block device's model matches inventory
4. For drives with expected UUID: filesystem UUID matches
5. For sdb (no expected UUID): wipefs -n is empty
6. NVMe root has ≥250G free
7. No process has files open under /mnt/storage (`lsof /mnt/storage`)
8. SMART status on each drive is PASSED
9. No active rsync/cp/mv/tar/dd/btrfs-{send,balance,scrub} processes
10. `/etc/fuse.conf` exists (role will edit it; just confirm presence)
11. mergerfs binary present and version ≥ 2.32 (for `category.create=ff` and current `minfreespace` semantics)

The script outputs a green/red checklist. The user runs it manually, fixes anything red, then runs the playbook. The role itself ALSO runs these checks (defense in depth) but the standalone script gives the human a fast feedback loop without committing to a play.

---

## Reference: what the human did manually that the role should reproduce

- `mkfs.btrfs -L primary /dev/sda1` (already done; role only re-formats with `--force-wipe`)
- `wipefs -a /dev/sdb` (done — disk is fully blank, ready for fresh GPT + partition + mkfs)
- fstab entries above
- Created `/mnt/sda1`, `/mnt/storage_cache`, `/mnt/storage`, `/mnt/backup3`, `/mnt/backup_pool` as directories before mounting
- Currently no mergerfs is active; current `/mnt/storage` is sda direct

The role should pick up the system in its **current** state (sda direct-mounted at `/mnt/storage`) and transition cleanly to the target state without data loss.
