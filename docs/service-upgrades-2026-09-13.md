# Home service upgrades — 2026-09-13

Follow-up to the [initial network audit](service-update-audit-2026-09-13.md).
The user authorized Jellyfin, Uptime Kuma, Coolify, and replacement of the
archived DDNS updater, running independently in parallel. Versions are pinned
in Ansible so routine deployment does not silently cross release lines.

| Service | Before | Target | Deployment status |
|---|---|---|---|
| Jellyfin (andromon) | 10.11.11 | 12.0.0, LinuxServer `12.0ubu2604-ls48` | Verified |
| Uptime Kuma (tentomon) | 1.23.17 | 2.5.4 | Verified, including repaired history |
| Coolify (wormmon) | 4.1.2 | 4.3.19 | Verified |
| Cloudflare DDNS (tentomon) | Archived `oznu/cloudflare-ddns:latest` | `favonia/cloudflare-ddns:1.17.0` | Verified |

## Jellyfin

Preflight found no active playback, six users without case collisions, and no
external plugins. A consistent archive was created with Jellyfin stopped:

- Host: andromon
- Archive: `/opt/docker/data/jellyfin-upgrade-backups/20260913T194909/config.tar`
- Size: 3,088,599,040 bytes; previous image metadata is beside the archive.
- Previous image: `lscr.io/linuxserver/jellyfin@sha256:0dd18f8de37c7cfbe76877a8b928943b3588beb9678fb223121914ec9a3cdf7c`

The migration and required full library scan completed. HTTPS reports
`Healthy`; authenticated API access works using the modern authorization
header. Six users, both Movies/Shows libraries, and 197 movies were preserved.
A real media range request returned HTTP 206 with 1,024 bytes, and a QSV H.264
encode passed as the container user. The old nullable encoder preset needed
normalization to `auto`; comparison with the backup confirmed the other
encoding settings were preserved. Ansible now handles that normalization.

The Homepage sessions widget needed `version: 2` to select supported API
routes. Its template and live configuration were updated; both widget
endpoints returned HTTP 200. Homepage was restarted with its existing image,
whose image ID stayed unchanged. The full template check then reported no
configuration drift, and a rerun completed with **changed=0, failed=0**.
Seerr uses Plex and had no Jellyfin connection to update.

A scoped rerun completed with **changed=0, failed=0**. Rollback requires stopping
Jellyfin, preserving the migrated configuration separately, restoring the
complete pre-upgrade archive, and using the previous image. An older image
alone cannot undo the database migrations.

## Uptime Kuma

The stopped data directory, including SQLite journal files, was archived before
the v1-to-v2 migration:

- Host: tentomon
- Archive: `/opt/docker/backups/uptime-kuma/pre-upgrade-20260913T194832-u2xrx7y8/data.tar`
- Size: 381,255,680 bytes; `image.txt` records the previous tag and image ID.

The baseline contains 13 active monitors (10 HTTP, three ping), one user,
one existing Telegram notification configuration, 12 notification links, and
about 2.55 million heartbeat records. The v2 migration aggregates historical
heartbeats into minute/hour/day statistics, retains important events, and
removes older non-important raw samples as part of its normal conversion. It
takes substantial time on the Pi; no additional history was pruned to accelerate
it. The original raw history remains in the pre-upgrade backup. HTTP 200 and Docker health alone are insufficient:
the migration page returns both, so Ansible also waits for the normal UI.

The migration completed after approximately 86 minutes. The database explicitly
reports `migrated`; HTTPS serves the normal Uptime Kuma UI, and the 2.5.4
container is healthy with zero restarts. Monitor/user/notification/link counts
remain 13/1/1/12. The scoped Ansible rerun completed with **changed=0, failed=0**.

Both backup and live SQLite integrity checks passed. Every shared monitor,
user, notification, and notification-link field matches the backup, and all
399 important events remain. All 13 monitors produce new cycles: twelve are
up; Ollama was already down with HTTP 502 before this maintenance.

Detailed history verification identified a timezone defect in the upstream
2.5.4 converter: it parses UTC-naive stored timestamps in the process timezone,
then groups daily statistics in UTC using a fresh calculator per original day.
Neighboring days can overwrite shifted buckets. An independent isolated test
with 2,880 UP samples retained only 2,460 in Pacific time, but all 2,880 in UTC.
Changing timestamps alone cannot recover overwritten counts. See the
[converter](https://github.com/louislam/uptime-kuma/blob/2.5.4/server/database.js#L914-L951)
and [calculator](https://github.com/louislam/uptime-kuma/blob/2.5.4/server/uptime-calculator.js#L300-L315).

The repair replays the union of the complete v1 archive and new v2 raw
heartbeats, deduplicating by verified IDs. It uses the pinned image's unchanged
calculator with explicit UTC parsing and database writes disabled. A separate
Python utility independently checks every bucket's counts and available ping
statistics, rejects changed source data, and replaces only the three statistics
tables in one transaction. It verifies SQLite integrity, foreign keys, and
identical fingerprints of every non-statistics table before committing.

Eight SQLite recovery tests and five tests against the actual vendor calculator
passed, covering timezone overlap, retention boundaries, statuses, duplicate
IDs, stale snapshots, invalid counts, and transaction rollback. A full-data
rehearsal passed on an isolated copy: 2,552,255 samples (the original history
plus 133 newer samples), 1,822 daily buckets, 9,360 hourly buckets, and 17,501
minutely buckets. All 28,683 buckets validated; non-statistics data stayed
identical. The isolated replay container had no network or application startup.

Repair artifacts are private on tentomon under:
`/opt/docker/backups/uptime-kuma/aggregate-repair-m2m8j9kq`.
They include the original database, rehearsal snapshot, source/aggregate JSONL,
validation manifests, and a success marker binding the verified recovery tools
and local container image. These files include private application data and
are not committed to the repository.

The role now reads the database migration marker, including data-only restores.
It runs v1 conversion in UTC and restores the configured timezone after both
normal UI readiness and the `migrated` marker are verified. Reruns wait for an
active migration; interrupted, stopped migrations require restoration. This
avoids repeating the timezone defect without changing the saved UI timezone.

The final stopped repair completed successfully. Its complete pre-repair backup
is `/opt/docker/backups/uptime-kuma/aggregate-repair-m2m8j9kq/final-yvfyvteg/before-repair-data.tar`
(412,590,080 bytes). The final replay included **2,552,434 samples**: all
2,552,122 original heartbeats plus 312 new samples. All **28,745 buckets**
validated (1,822 daily, 9,360 hourly, 17,563 minutely); the statistics-only
transaction passed integrity, foreign-key, and non-statistics fingerprint
checks before committing. Kuma restarted after a **455-second** maintenance
pause, clearing its cached summaries.

Independent post-repair verification passed: all 1,822 original UTC
monitor-days are present, all 399 important events remain, and the original
database's SHA-256 matches the archived database. All 28,602 closed buckets
still within retention matched the manifest, with zero mismatches. The 26 open
buckets contained no lost samples; 117 oldest minute buckets had legitimately
aged out during the checks. The previously damaged May 1 Plex day again
contains all 1,438 UP samples.

Every shared monitor, notification, notification-link, and user field still
matches the v1 backup. All 13 monitors produced fresh cycles: twelve UP and the
pre-existing Ollama HTTP 502 unchanged. SQLite integrity, HTTPS UI, and
container health passed. The final scoped Ansible run returned **changed=0,
failed=0**.

The repository recovery utility also rejects gaps in the post-backup heartbeat
ID range, preventing a later replay from erasing newer history whose raw samples
have expired. The successful repair included the complete 312-sample ID range.
Do not rerun these recovery tools as routine maintenance.

## Coolify

The vendor upgrade workflow moved the control plane to 4.3.19, realtime to
1.0.19, PostgreSQL to 15.19, Redis to 7.4.11, and pulled helper 1.0.16.
Sentinel remains at 0.0.22 as selected by the live Coolify version feed; the
proxy is unchanged. Release notes mention a newer Sentinel, but the deployed
vendor reconciliation uses the feed.

- Host: wormmon
- Backup directory: `/opt/docker/data/coolify-upgrade-backups/4.1.2-to-4.3.19-7y7hybt8`
- Contents: validated PostgreSQL custom dump (~13 MB), globals, configuration,
  SSH/deployment archive, and a sanitized container baseline.
- Permissions: root-only directory 0700 and backup files 0600.

All 65 containers were running after the upgrade, with no unhealthy declared
health checks. All 59 tenant containers, the proxy, and Sentinel retained the
same container IDs, image IDs, and start times. API health and direct/proxied
login endpoints passed; no migrations or deployments remained pending. The
repository-specific backup traversal permissions were restored and checked.
A scoped rerun completed with **changed=0, failed=0**.

Rollback requires restoring the old control-plane database and configuration
alongside the retained old images. Keep tenant containers and proxy running;
changing only the Coolify image is insufficient. The role rejects automatic
downgrades. See the [Coolify runbook](wormmon-coolify.md).

## DDNS replacement

The existing `cloudflare-ddns` container was replaced by Favonia 1.17.0 using
the existing scoped API token. It runs as UID/GID 1000 with a read-only root
filesystem, all capabilities dropped, and `no-new-privileges`.

The existing behavior is preserved: update the single unproxied IPv4 A record
`ddns.batjaa.site`, leave IPv6 disabled, run on startup and every five minutes,
and retain DNS records on shutdown. Cloudflare API checks before and after
showed the same record and TTL; public DNS resolved to the detected WAN IPv4.
Startup and the next scheduled five-minute check confirmed the existing record
was already correct. A scoped
rerun completed with **changed=0, failed=0**.

There is no persistent application state to migrate. The previous image remains
locally available; rollback uses the old role configuration and the existing
vault token. See the [DDNS runbook](services.md#cloudflare-ddns).

## Validation and limits

Backup archive listings and the PostgreSQL dump listing were verified. Full
restore rehearsals were not performed. No host package or firmware updates
were included. Other audit findings remain open, including MySQL lifecycle,
PiKVM and MikroTik maintenance, and the other outdated application images.
The combined playbook syntax check passed. Existing unrelated working-tree
changes were preserved.

Reapply each service independently:

```bash
ansible-playbook main.yml -l andromon --tags jellyfin
ansible-playbook main.yml -l tentomon --tags uptime-kuma
ansible-playbook main.yml -l wormmon --tags coolify
ansible-playbook main.yml -l tentomon --tags cloudflare-ddns
```

## Upstream references

- [Jellyfin 12 release and migration notes](https://github.com/jellyfin/jellyfin/releases/tag/v12.0)
- [Uptime Kuma v1-to-v2 migration](https://github.com/louislam/uptime-kuma/wiki/Migration-From-v1-To-v2)
- [Coolify 4.3.19 release](https://github.com/coollabsio/coolify/releases/tag/v4.3.19)
- [Coolify version feed](https://cdn.coollabs.io/coolify/versions.json)
- [Favonia DDNS 1.17.0](https://github.com/favonia/cloudflare-ddns/releases/tag/v1.17.0)
