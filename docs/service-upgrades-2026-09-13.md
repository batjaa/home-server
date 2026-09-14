# Home service upgrades — 2026-09-13

## Current handoff — 2026-09-14

- **Completed:** the earlier Coolify/application upgrades; Immich, Paperless,
  and Nextcloud database patches; Prowlarr, Bazarr, Sonarr, Radarr container
  rebuilds; and movie-agent's Go/Huma/runtime update. Bazarr's missing Sonarr
  credential was also repaired in Ansible.
- **Remaining monitoring:** Grafana, Prometheus, cAdvisor, and Tentomon's
  node_exporter.
- **Remaining hosts/storage:** Andromon Docker tooling and mergerfs;
  Andromon/Tentomon OS updates.
- **Greymon recovered:** Ollama 0.34.0 is current; GPU inference from Open
  WebUI and the HTTPS proxy pass. Greymon's PiKVM is reachable again.
- **Access/coverage gaps:** MikroTik and PiKVM updates; Greymon Windows/GPU
  driver versions, Epson firmware, and the unidentified device on port 8123
  remain unverified.
- The earlier Coolify stop-job incident is recovered, but its trigger remains
  unexplained. Unrelated local indexer/search changes remain uncommitted.

Detailed verification and rollback records follow in chronological sections.

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
proxy was unchanged during that control-plane upgrade; the later application
maintenance below updated it to 3.6.25. Release notes mention a newer Sentinel, but the deployed
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

## Coolify application and host maintenance

Completed after the user approved the application update backlog. Execution
finished September 13 PDT (September 14 UTC), on wormmon, across all 13 active
Coolify applications. Changes were committed and pushed from isolated
worktrees to each application's main branch (TSAS uses master), then explicitly
deployed and verified. The stopped experimental Heyanda application was excluded.

| Application | Verified database runtime | Deployed commit |
|---|---|---|
| bet-mn | PostgreSQL 16.15 | `87ff134c4227` |
| demo | PostgreSQL 16.15 | `c78d8a1977b1` |
| fotopass | MySQL 8.4.11 | `6a30db95aeec` |
| groove | PostgreSQL 16.15 | `7796767e5eee` |
| Heyanda | MySQL 8.4.11; Neo4j 5.26.30 | `6700bd8db6a9` |
| kedge | PostgreSQL 16.15 | `83bfbe5356e2` |
| plotling | MySQL 8.4.11; Neo4j 5.26.30 | `fdcb7b113c96` |
| summit-todo | PostgreSQL 16.15 | `d1a8357e05dd` |
| tech-nomads | MySQL 8.4.11 | `de53b756a00b` |
| tendies | MySQL 8.4.11 | `af41e8ae957d` |
| tipped | PostgreSQL 16.15 / PostGIS 3.5.7 | `56cf4bb4765a` |
| tsas | MySQL 8.4.11 | `78ce1b3442c2` |
| otor | PostgreSQL 16.15 | `b13c5e4eff1e` |

Six MySQL databases moved from the final 8.0.46 release to 8.4.11 LTS.
The six regular PostgreSQL databases moved from 16.13/16.14 to 16.15.
Tipped moved from PostgreSQL 16.4 / PostGIS 3.4.3 to PostgreSQL 16.15 /
PostGIS 3.5.7, using
`postgis/postgis:16-3.5-alpine@sha256:47e961a569fd52ff31f0fe205ed91eeab17d9f5fff6722e6d7ea6b588748b293`.
All three PostGIS extensions were upgraded and a spatial query passed.
Both Neo4j instances moved from 5.26.23 to 5.26.30 on the existing LTS line.

Kedge's Kroki server and Mermaid, Excalidraw, and BPMN sidecars moved together
from 0.31.0 to 0.32.1. All four formats produced SVGs in an isolated rendering
check. Live container image versions match the new pins.

Composer security remediation passed for all 13 app lockfiles; all 15 npm
lockfiles used by the Coolify builds report zero known vulnerabilities at the
time of verification. Package validation, platform requirements, targeted PHP
tests, and affected frontend builds passed. PHP/Node/Caddy base images were
refreshed for the rebuilds. This is a security and compatibility update, not an
assertion that every dependency is on its newest release. Groove's separate
mobile dependency tree was outside the Coolify dependency update scope.

Kedge's web build uses Next 16.3.5 and Vitest 4.1.11; its build, tests, and CI
passed. Plotling and Tipped needed Nova 5.10.2 to fix console command discovery
with the updated Laravel packages. Regression tests cover command listing and
Tinker startup. Plotling's full 87-test browser suite and Tipped's 44 targeted
PHP tests passed. All 18 configured GitHub checks across seven repositories
finished successfully; six repositories have no configured checks for these
commits and were verified with local/build/runtime checks.

### Backup and migration evidence

- Remote private backup root:
  `/opt/docker/data/app-upgrade-backups/20260914T042636Z` on wormmon.
- Off-host private copy and verification artifacts:
  `/Users/batjaa/Downloads/coolify-upgrades-2026-09-13`.
- The database archive is 212,211,829 bytes; SHA-256 checksums are recorded in
  `archive-checksums.json`. The separate `host-maintenance.tar.gz` contains host
  package/configuration evidence and the previous proxy configuration.
- Logical backups from all 13 relational databases were restored into isolated
  containers running the target database versions. Existing deployed PHP images
  passed PDO connect/read/write/rollback checks against those restored copies.
- Each production migration drained writers, stopped the database, archived its
  data volume, and verified a cold copy before starting the upgraded database.
  Exact per-table row counts matched while writes were paused. Both Neo4j
  databases retained their node and relationship counts.
- Original volumes remain retained. New volumes use `mysql-data-v84`,
  `postgres-data-v16-15`, `postgres-data-v16-15-postgis35`, or
  `neo4j-data-v5-26-30`, prefixed with the Coolify application UUID.
- The five existing production secure notes for Fotopass, MyTendies, Heyanda,
  Tech Nomads, and TSAS were updated with backup and volume references and
  read back successfully. Their secrets were preserved.

For rollback, stop writers and preserve current data before restoring the
matching old database image, configuration, and pre-upgrade volume/archive.
The retained old volumes are snapshots, not replicas: they do not contain
writes made after the upgrade. Reconcile those writes before switching back;
never start an older database image against an upgraded data directory.

### Wormmon host and ingress

Workers were drained and databases stopped cleanly before the shared Docker
maintenance window. Databases recovered first, then apps, workers, schedulers,
and ingress. Final versions:

| Component | Before | Verified after |
|---|---|---|
| Docker Engine | 29.7.2 | 29.8.0 |
| containerd | 2.3.3 | 2.3.5 |
| Docker Compose | 5.4.0 | 5.5.1 |
| Buildx | 0.36.1 | 0.37.1 |
| Traefik | 3.6.23 | 3.6.25 |
| node_exporter | 1.11.1 | 1.12.1 |

APT installed 31 package updates. Ubuntu deferred
`python3-software-properties` and `software-properties-common` under its
phased rollout; that policy was not overridden. No reboot is required and no
systemd units are failed. Node exporter was updated through the existing
Ansible role, pinned in the local wormmon host variables, and its second
scoped run passed with **changed=0, failed=0**.

### Final verification

All 65 running containers passed their declared health checks; containers
without a health check were confirmed running. All 13 app images match the
tested commit and Composer lockfile hash. All eight configured Nightwatch
connections passed `nightwatch:status`: Fotopass, Groove, Heyanda, Kedge,
Plotling, Tech Nomads, MyTendies, and TSAS.

All 16 public URLs loaded in a fresh Chrome context with HTTP 200 after
redirects, no uncaught JavaScript errors, and no detected broken images.
Screenshots and page results are in `browser-proof/` in the off-host evidence
directory. These checks cover public pages; they do not establish that every
authenticated, payment, or paid AI workflow was exercised in production.

Temporary commit pins and auto-deployment changes were restored to each
application's previous settings and read back through the Coolify API. The
clean local Plotling main checkout was fast-forwarded to its deployed commit.
Other existing local working-tree changes were preserved.

## Validation and limits

For the initial Jellyfin, Uptime Kuma, Coolify control-plane, and DDNS work,
backup archive listings and the PostgreSQL dump listing were verified; full
restore rehearsals were not performed. The later Coolify application work
below includes database restore rehearsals and wormmon host packages.
PiKVM, MikroTik, other hosts, and application updates outside the scope recorded
here remain separate maintenance work. The combined playbook syntax check
passed. Existing unrelated working-tree changes were preserved.

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

## Application follow-up — 2026-09-14

The user selected seven remaining applications on andromon. All seven were
updated through their existing Ansible roles and verified. Their image versions
are now pinned in role defaults; Immich server and ML advance together.

| Application | Before | Verified after |
|---|---|---|
| Immich server + ML | 3.1.0 | 3.2.0 |
| Open WebUI | 0.11.0 (`main`) | 0.11.3 stable |
| Home Assistant | 2026.8.1 | 2026.9.2 |
| Homepage | 1.13.2 | 2.3.0 |
| Plex | 1.43.3.10861 | 1.43.4.10903, LinuxServer `1.43.4.10903-e5521bd8c-ls324` |
| Whisparr | 3.3.7.979 | 3.5.0.1585, pinned by digest on v3 |
| SABnzbd | 5.1.0 | 5.1.3, LinuxServer `5.1.3-ls273` |

Immich's database and Redis image IDs were preserved and their existing
images pinned by digest. This follow-up did not upgrade database engines,
operating systems, Docker, or network infrastructure.

### Backups and rollback

Each app was stopped cleanly before its backup. Plex had no active viewers;
Whisparr and SABnzbd had no queued work. Backups are private on andromon:
`/opt/docker/data/application-upgrade-backups/20260914T142228Z`.

The directory contains pre-upgrade container definitions/image IDs, consistent
configuration archives, database checks, and verification records. Immich's
665,961,722-byte custom PostgreSQL dump was restored into a separate container
with networking disabled. Asset/user/album counts matched the backup. SQLite
integrity checks passed for Home Assistant, Open WebUI, Whisparr, and SABnzbd.

The off-host recovery archive and browser/runtime evidence are stored in:
`/Users/batjaa/Downloads/andromon-app-upgrades-2026-09-14`.
The archive is 6,587,340,800 bytes; its SHA-256 is recorded in
`archive-checksum.json`. All eight database/configuration backup files inside
it matched independently computed server-side SHA-256 checksums.

Media originals were not duplicated as part of these application backups.
SABnzbd's existing `Downloads` directory was excluded from its configuration
archive; its configuration and history were backed up. Plex's library metadata
was archived, while its separate media mounts were preserved.

Rollback requires stopping the affected app, preserving any new writes, then
restoring its matching pre-upgrade configuration/database and old image.
An image downgrade alone does not undo database or Home Assistant registry
migrations. Keep the original images and backups until the updated apps have
been exercised sufficiently.

### Verification

- **Immich:** all 80,581 assets remain. Authenticated metadata search and an
  existing image thumbnail succeeded. No increase in failed background jobs
  was detected. Both server and ML containers are healthy.
- **Home Assistant:** the target image passed configuration checking against
  an isolated writable copy, including device-registry migration. Authenticated
  API reports `RUNNING` on 2026.9.2. All 196 entity IDs remain, with the same
  20 pre-existing unavailable entities and no newly unavailable entities.
  The temporary verification login was revoked afterward.
- **Open WebUI:** health and version endpoints pass. Both users, all 12 chats,
  and all three saved models remain; the `chat.timer_at` migration is present.
  Its authenticated movie-agent connection returns seven available API paths.
  Ollama on greymon remains unreachable, as noted in the original audit, so
  chat generation could not be verified.
- **Homepage:** dashboard and 32 widget requests passed after the other apps
  recovered, with no widget errors or uncaught JavaScript exceptions. The
  existing authentication behavior was preserved; v2 authentication is optional.
- **Plex:** three libraries, five account records, 24,088 metadata records,
  and 24,078 media items/parts remain. Authenticated media retrieval returned
  HTTP 206 with a 1,024-byte range. Its authenticated web UI loaded correctly.
  Unauthenticated `/` is an API endpoint and returns 401; the web UI is `/web/`.
- **Whisparr:** 744 movie records, 209 files, one root folder, one download
  client, and two indexers remain. API version, queue, and integration
  configuration checks passed. Version 3.5 introduced an `AllowedHostsCheck`
  warning; the role now configures the seven existing public/internal hosts
  and restarts Whisparr when that list changes. All seven hosts passed an
  authenticated API request, an unexpected hostname was rejected, and the
  new health warning cleared.
- **SABnzbd:** all 1,680 history entries remain; the queue is idle and both
  configured news servers remain present. API and browser checks passed.

All eight application containers match their verified target image IDs and
pass declared health checks. Startup-log checks found no ERROR/FATAL/CRITICAL
lines. Seven application UIs loaded in Chrome with no uncaught JavaScript
exceptions; screenshots are in the off-host evidence directory.

The scoped Ansible rerun passed with **failed=0** and no container replacements.
Its six reported changes were four directory-permission normalizations and
the existing SABnzbd temporary server-fragment creation/removal workflow.
Existing unrelated repository changes were preserved.

Primary release references:
[Immich 3.2.0](https://github.com/immich-app/immich/releases/tag/v3.2.0),
[Open WebUI 0.11.3](https://github.com/open-webui/open-webui/releases/tag/v0.11.3),
[Home Assistant 2026.9](https://www.home-assistant.io/blog/2026/09/02/release-20269/),
[Homepage 2.3.0](https://github.com/gethomepage/homepage/releases/tag/v2.3.0),
[Homepage authentication](https://gethomepage.dev/installation/#security-authentication),
[LinuxServer Plex](https://docs.linuxserver.io/images/docker-plex/),
[Whisparr image](https://hotio.dev/containers/whisparr/), and
[SABnzbd 5.1.3](https://github.com/sabnzbd/sabnzbd/releases/tag/5.1.3).

## Immich database follow-up — 2026-09-14

The PostgreSQL lifecycle/patch and Redis patch items from the audit are complete.
Immich server and ML remain on 3.2.0.

| Component | Before | Verified after |
|---|---|---|
| PostgreSQL | 14.19 | 18.6 (`18.6-1.pgdg12+2`) |
| VectorChord | 0.4.3 | 1.1.1 |
| pgvector | 0.8.1 | 0.8.5 |
| Redis | 6.2.23 | 6.2.24 |

The official Immich `18-vectorchord1.1.1-pgvector0.8.5` image was inspected and
still contains PostgreSQL 18.4. The Ansible role builds
`local/immich-postgres:18.6-vectorchord1.1.1-pgvector0.8.5-r1` from its pinned
digest, updating the server, client, and libpq packages to the pinned PGDG
18.6 package revision. This retains Immich's extension binaries and health
check. Future PostgreSQL patch releases require updating the package/image
pins and rebuilding this layer. The database runs only on `immich-net`, with
no published database port. PostgreSQL 18 is supported through November 2030.

### Migration, data verification, and backups

A rehearsal restored the earlier backup into an isolated PostgreSQL 18
container. It exposed a parent-directory permission issue with the new
versioned PGDATA layout; the helper was corrected before production migration.
The rehearsal subsequently passed, including rebuilding the vector indexes.

Production was paused for a fresh custom-format dump and restore into a
separate directory. Every one of the **66 public table row counts**, plus a
digest of all asset IDs, matched before the application was started. The
database has **80,899 asset rows** (including deleted/hidden entries), two
users, four albums, 159,125 face embeddings, and 80,501 smart-search embeddings.
The authenticated API still reports **77,237 photos + 3,344 videos = 80,581
visible assets**. PostgreSQL checksums are enabled, checksum failures are zero,
and there are no invalid indexes. Metadata search, a 19,318-byte thumbnail,
and natural-language smart search all passed. Failed job counts did not increase.

Redis was saved and stopped before its original anonymous volume was copied
to the persistent `/opt/docker/data/immich-redis` bind mount. All **22 keys**
matched before Immich restarted. The original Redis volume was retained.

Private recovery files on andromon:
`/opt/docker/data/immich-db-upgrade-backups/20260914T174902Z`.
The directory contains the 665,788,331-byte database dump, roles backup,
original container definitions, table/asset-ID verification, and Redis snapshot.
The off-host copy is at
`/Users/batjaa/Downloads/immich-db-upgrade-2026-09-14`.
The database SHA-256 matches the server copy, and all eight supplemental
configuration/Redis file checksums match the off-host archive.

The PostgreSQL 14 data remains at `/opt/docker/data/immich-db`; PostgreSQL 18
uses `/opt/docker/data/immich-db-pg18/18/docker`. Uploaded media was neither
moved nor duplicated. Rollback requires stopping Immich, preserving any new
writes, and restoring the old PostgreSQL image/mount and Redis definition
from `containers-before.json` together with the pre-migration role settings.
Never point PostgreSQL 14 at the PostgreSQL 18 directory. The helper refuses
existing target directories and only writes its migration marker after the
restore and Redis copy have been verified; ordinary deploys refuse to start
an empty replacement database when the legacy database is present.

An Immich-managed backup also completed successfully using its PostgreSQL
18.6 client: `immich-db-backup-20260914T105319-v3.2.0-pg18.6.sql.gz`,
666,421,249 bytes. This confirms the application's scheduled backup mechanism
can back up the new database version.

Three migration safety tests passed, as did Ansible syntax validation. The
ordinary Immich role rerun reported **changed=0, failed=0**; an explicit
migration rerun skipped the completed migration and only refreshed the helper.

Sources: [Immich database compatibility](https://docs.immich.app/administration/postgres-standalone/),
[Immich 3.2.0 compatibility constants](https://github.com/immich-app/immich/blob/v3.2.0/server/src/constants.ts),
[Immich backup/restore](https://docs.immich.app/administration/backup-and-restore/),
[Immich database image build](https://github.com/immich-app/base-images/blob/main/postgres/Dockerfile),
and [PostgreSQL support policy](https://www.postgresql.org/support/versioning/).

### Immich browser and WebSocket follow-up

Authenticated browser verification found that the UI displayed “Server Offline”
while REST requests and photos worked. Direct WebSocket handshakes returned
HTTP 101; the SWAG route returned HTTP 400. The Immich proxy template repeated
`Upgrade` and `Connection` headers already set by SWAG's included `proxy.conf`.
Those duplicate directives were removed from the Immich template. Only that
proxy file was applied, nginx configuration validation passed, and nginx was
reloaded without restarting its container.

The final authenticated browser check reports **Server Online**, HTTP 200,
31 loaded images, zero WebSocket handshake errors, and zero uncaught JavaScript
errors. Evidence is in `immich-after.png` and `browser-verification.json` in
the off-host recovery directory. Temporary verification login sessions were
logged out. The new Immich-managed database backup also passed `gzip -t`.

References: [Immich reverse proxy requirements](https://docs.immich.app/administration/reverse-proxy/)
and [SWAG's shared proxy headers](https://github.com/linuxserver/docker-swag/blob/master/root/defaults/nginx/proxy.conf.sample).

## Selected application follow-up — 2026-09-14

The user selected the five remaining applications from the top audit list.
All five were deployed through their Ansible roles, with reviewed releases
pinned in defaults.

| Application | Before | Verified after |
|---|---|---|
| Paperless-ngx | 3.0.5 | 3.1.3 |
| Nextcloud | 34.0.2 | 34.0.4 (`34.0.4-apache`) |
| Beets | 2.13.1 | 2.14.0, LinuxServer `2.14.0-ls352` |
| Tautulli | 2.17.2 | 2.18.1, LinuxServer `v2.18.1-ls244` |
| Navidrome | 0.63.2 | 0.64.0 |

Paperless PostgreSQL/Redis and Nextcloud MariaDB remain on their original image
IDs, now pinned by digest. Their engine patch upgrades remain separate audit
items. The roles now preserve existing database-directory ownership. Beets
and Tautulli configuration groups match their container PGID; this fixes the
previous Beets config ownership/reset/restart cycle on every Ansible rerun.

### Backups and recovery

Private backups on andromon:
`/opt/docker/data/selected-app-upgrade-backups/20260914T181027Z`.
Original container definitions/image IDs are saved in the root of that directory.

Each app was stopped before its configuration archive; Nextcloud also entered
maintenance mode. Paperless's PostgreSQL dump was restored into an isolated
container with **all 74 table counts matching**. Nextcloud's MariaDB dump was
restored into an isolated container with **all 131 table counts matching**.
The three SQLite catalogs passed integrity checking. Configuration/document
archives were compared byte-for-byte against their stopped sources.

Nextcloud's full user-data archive is **31,680,860,160 bytes** and its application
archive is **879,779,840 bytes**. The full user-data archive is retained on the
server. All five apps' configuration/database backups, plus Paperless's document
archive, have off-host copies with matching SHA-256 checksums at:
`/Users/batjaa/Downloads/selected-app-upgrades-2026-09-14`.
Music originals were not duplicated; the update does not modify those files.

Paperless's pinned-image deployment recreated its anonymous Redis volume.
Verification caught the change. The pre-update RDB snapshot had eight unexpired
keys; seven were missing from the new instance. Those seven keys were restored
without overwriting newer keys, and the overlapping key matched. The combined
state was saved with Paperless stopped and copied into the explicit persistent
mount `/opt/docker/data/paperless-redis`. Both anonymous volumes and the original
and recovered RDB snapshots were retained. The recovery evidence is included
in the off-host archive. Redis remains on the same engine image/version.

To roll back an app, stop it, preserve any post-update writes, and restore its
matching database/configuration archive and previous image together. For
Nextcloud, the backup includes maintenance mode; restore the matching SQL,
application files, and user data as needed, then disable maintenance mode after
checking consistency. Navidrome's ID migration cannot be undone by changing
only the image tag; its old SQLite database must be restored too.

### Verification and application notes

- **Paperless:** 10 documents and three users retained; authenticated API and
  PDF preview passed. `document_sanity_checker --no-progress-bar` reported no
  issues. The container health check passes, including after Redis recovery.
- **Nextcloud:** all 50 enabled apps remain enabled; 10,329 file-cache records,
  one user, two shares, two calendars, and two address books remain. Upgrade
  completed, maintenance is off, and `needsDbUpgrade` is false. Authenticated
  WebDAV returned HTTP 207, capabilities returned the new version, and
  `occ integrity:check-core` passed.
- **Beets:** all ten configured plugins load on 2.14.0. Its existing catalog is
  empty (zero items/albums); the query UI and catalog API work. No import or
  retag operation was run against the music collection.
- **Tautulli:** 226 history records, six users, and three libraries retained.
  Authenticated version/activity/library APIs passed. Its browser displays the
  expected Plex SSO login page; a new Plex SSO login was not exercised.
- **Navidrome:** an isolated migration rehearsal preserved all 17,163 songs,
  3,356 albums, 3,947 artists, four users, annotations, and music paths/sizes,
  with no foreign-key violations. Production retained the same music/user
  counts and path digest. Its enabled startup scanner imported 36 existing
  playlists (385 entries) and cleaned up one orphaned artist annotation which
  referenced no artist in either database; it had no star/rating. Streaming
  returned HTTP 206 with a 1,024-byte audio range. Incomplete artist metadata
  produced artwork lookup warnings, including one empty-artist Deezer error;
  library access and audio streaming passed.

Navidrome 0.64 re-encodes internal IDs. Clients caching IDs or offline downloads
may need to resync. Its public-sharing setting remains disabled.

All five browser pages returned HTTP 200 with no uncaught JavaScript errors.
Paperless, Nextcloud, and Navidrome were checked after login; Beets's empty query
interface and Tautulli's SSO entry page were checked alongside their APIs.
Screenshots and runtime/API verification are in the off-host backup directory.

The final scoped Ansible rerun passed with **changed=0, failed=0** and syntax
validation passed. All 52 supplemental verification/recovery file checksums
also match the server copies. Existing unrelated repository changes were
preserved.

Sources: [Paperless 3.1.3](https://github.com/paperless-ngx/paperless-ngx/releases/tag/v3.1.3),
[Nextcloud 34.0.4](https://nextcloud.com/changelog/#latest34),
[Beets 2.14.0](https://github.com/beetbox/beets/releases/tag/v2.14.0),
[Tautulli 2.18.1](https://github.com/Tautulli/Tautulli/releases/tag/v2.18.1),
and [Navidrome 0.64 migration notes](https://github.com/navidrome/navidrome/releases/tag/v0.64.0).

## Paperless and Nextcloud database patches — 2026-09-14

The database-only follow-up supersedes the deferred database patches in the
selected application section above. Application releases remain Paperless 3.1.3
and Nextcloud 34.0.4.

| Component | Before | Verified running |
|---|---|---|
| Paperless PostgreSQL | 16.14 | 16.15 (`16.15-trixie`) |
| Paperless Redis | 7.4.10 | 7.4.11 (`7.4.11-alpine`) |
| Nextcloud MariaDB | 11.8.8 | 11.8.9 |

All three image references are pinned by release and digest in role defaults.
PostgreSQL retains Debian 13/Trixie and glibc 2.41, matching the existing
cluster's collation version. The cluster only has `plpgsql` installed and no
logical replication slots, so the release's extension reindex and output-plugin
configuration caveats do not apply.

### Backup and rehearsal

Fresh backups are at
`/opt/docker/data/app-db-patch-backups/20260914T190334Z` on andromon.
Each application was stopped before its SQL dump and configuration archive;
Nextcloud was also placed in maintenance mode. Database containers were shut
down cleanly before cold archives were created and compared against the source.
Paperless's cold archive includes the Redis bind directory.

For each SQL database, two isolated containers running the target image were
verified: a fresh logical restore and an upgrade of a copy of the existing
physical database. Both ran with `--network none`. All **74 Paperless table
counts** and **131 Nextcloud table counts** matched their paused sources in both
rehearsals. MariaDB table checks passed in both. Redis 7.4.11 loaded the copied
snapshot with all **12 unexpired keys**, serialized value hashes, and expiry
times preserved.

The backup archives contain database state and application configuration. This
database-only change does not modify document or user-file storage; their full
backups from the preceding application upgrade remain available at
`/opt/docker/data/selected-app-upgrade-backups/20260914T181027Z`.

Sources: [PostgreSQL 16.15 release notes](https://www.postgresql.org/docs/16/release-16-15.html),
[Redis 7.4.11 release notes](https://github.com/redis/redis/releases/tag/7.4.11),
[MariaDB 11.8.9 release notes](https://mariadb.com/docs/release-notes/community-server/11.8/11.8.9).

### Production verification and recovery

Both scoped Ansible deployments passed. A repeat run of
`--tags paperless,nextcloud` reported **ok=16, changed=0, failed=0**; syntax and
whitespace checks also passed. All five containers are running with their
original mounts, zero restart counts, and the expected image IDs. Paperless
reports Docker health `healthy`.

- **Paperless:** PostgreSQL reports `16.15-1.pgdg13+2`; Redis reports 7.4.11.
  There are no invalid indexes or recorded collation mismatches. All 10
  documents and three users remain; authenticated document listing and PDF
  preview pass. `document_sanity_checker --no-progress-bar` reports no issues.
  The production table comparison differs only by two newly recorded background
  tasks. Redis resumes normal worker activity after the snapshot rehearsal.
- **Nextcloud:** MariaDB reports `11.8.9-MariaDB-ubu2404-log` and confirms that
  no `mariadb-upgrade` is required within this release series. All 131 table
  counts match immediately after restart, including 10,329 file-cache records,
  one user, two shares, two calendars, and two address books. All 50 enabled
  apps remain enabled. Maintenance mode is off, no application database upgrade
  is pending, authenticated WebDAV returns 207, and both database table checks
  and Nextcloud core integrity checks pass.
- Authenticated browser checks reach Paperless's dashboard and Nextcloud's Files
  page with HTTP 200 and no uncaught JavaScript exceptions. Screenshots and
  browser result JSON are saved alongside the off-host backups.

MariaDB uses its normal libaio fallback because io_uring is unavailable on the
host. Isolated rehearsal networking also produces harmless address-discovery
warnings; neither prevented startup or table verification.

All six fresh SQL/configuration/cold-database backup files were copied to
`/Users/batjaa/Downloads/app-db-patches-2026-09-14` and matched their server-side
SHA256 manifests. The 33-file supplemental verification/recovery archive also
matches its server SHA256; it includes private original/current container
specifications, API inventories, comparisons, and logs. Ansible logs and the
verification scripts are retained off-host too. Rehearsal containers and their
four temporary database directories were removed after verification; backup
archives and verification results remain.

For rollback, stop the affected application and database containers, preserve
the current database directories under new names, restore the matching cold
archive with original ownership, revert that role's image pins to the previous
commit, and apply its scoped Ansible tag. Paperless's database cold archive also
restores Redis state. A rollback returns the database to backup time, so preserve
and account for any writes made since then. Nextcloud's app configuration backup
contains maintenance mode; turn maintenance mode off after database and
application checks. Do not extract a cold database archive over a running
cluster. Original database images remain on the server.


## Media applications and movie-agent — 2026-09-14

| Component | Before | Verified running |
|---|---|---|
| Prowlarr | 2.5.2.5491-ls158 | 2.5.2.5491-ls159 |
| Bazarr | v1.6.0-ls357 | v1.6.0-ls363 |
| Sonarr | 4.0.19.2979-ls321 | 4.0.19.2979-ls324 |
| Radarr | 6.3.0.10514-ls313 | 6.3.0.10514-ls316 |
| movie-agent | `agents/movie-agent:dev`, Go 1.25.10 / Huma 2.37.3 | `agents/movie-agent:2026.09.14`, Go 1.27.1 / Huma 2.39.1 |

The four media upgrades are LinuxServer rebuilds of the same application
versions. Their role defaults now pin explicit releases. Config-directory groups
match the containers' PGID, avoiding permission churn on repeated deployments.
Prowlarr's existing convergence script already matched the pending local changes;
its NZBFinder interactive-only profile and three application connections remain
unchanged. Those unrelated pending changes were not included in this commit.

Movie-agent's builder and nonroot Debian 13 distroless runtime are pinned by
digest. The build embeds a release version and uses `-trimpath`;
`/agent version` reports `movie-agent 2026.09.14` without requiring server
credentials, while an ordinary local build reports `dev`. The running binary's
build metadata confirms Go 1.27.1 and Huma 2.39.1. No API routes or operation IDs
were removed, and existing environment values and network connections remain.

### Backups and data verification

Server backups:
`/opt/docker/data/media-app-upgrade-backups/20260914T193226Z`.
Off-host copies:
`/Users/batjaa/Downloads/media-app-upgrades-2026-09-14`.

Each media app was stopped for its configuration/database archive. Archives were
compared against the source, extracted into separate directories, and the
restored SQLite databases passed integrity checks with all table counts matching:
Prowlarr **21**, Bazarr **17**, Sonarr **39**, Radarr **42** tables. All four
archives were copied off-host and their SHA256 digests matched. Movie-agent is
stateless; its original Docker image, source tree, and container specification
were preserved, with image/source archives also verified off-host.

- **Prowlarr:** two indexers, three applications, one download client, and all
  application profiles preserved; health endpoint reports no issues.
- **Sonarr:** 54 series, 6,054 episode records, and 3,303 episode files preserved.
  Download client, two indexers, Plex notification, and root-folder settings
  match. Only two new background-command rows appeared in the post-restart
  database comparison; root-folder free-space readings naturally changed.
- **Radarr:** all 212 movies and 197 movie files retained; table counts,
  indexers, download client, Plex notification, and root-folder settings match.
- **Bazarr:** 197 movies and two language profiles retained; all table counts
  initially matched after the image update. Its enabled Sonarr integration had
  an empty API key before this work, leaving zero shows/episodes and producing
  unauthorized SignalR errors. Ansible now reads current settings and supplies
  the vaulted key only when an enabled integration differs. Only
  `sonarr.apikey` changed. The SignalR connection now succeeds, Bazarr can read
  all 54 Sonarr series, and its TV catalogue has begun populating normally.

### Application checks

Movie-agent's new HTTP contract tests exercise all seven routes against mock
upstreams, request serialization, authorization, invalid inputs, and upstream
failure. Go race tests, `go vet`, and source vulnerability scanning passed.
A separate `govulncheck -mode=binary` scan of the actual deployed Linux binary
also found no known vulnerabilities. Both the default and embedded version
commands were tested without service credentials.

Production checks passed for movie-agent health, schema, authenticated ping,
library search, recent movies, queue, discovery, combined movie status, and
rejection of invalid requests. Successful request creation was tested against a
mock Seerr server only, avoiding real downloads. Open WebUI can authenticate to
movie-agent over its Docker network. All seven OpenAPI paths and operation IDs
remain available; Greymon/Ollama remains outside verified coverage.

Browser checks returned HTTP 200 without uncaught JavaScript exceptions for all
four media apps. Bazarr's interface was verified using its API key. Prowlarr,
Sonarr, and Radarr were verified at their login pages; their authenticated APIs
were tested separately. No new interactive login to those three UIs was performed.
Screenshots and browser JSON are stored with the off-host backups.

The final scoped Ansible rerun, including Bazarr's integration repair, reported
**ok=31, changed=0, failed=0, skipped=3**. All five containers run with their
original mounts and zero restart counts. The four existing Prowlarr convergence
tests also passed. Secrets remain in the vault and private backup artifacts.

For rollback, stop the affected media container, preserve its current config
directory, restore its cold archive with original ownership, and select the
previous image in the role before applying its tag. Keep any metadata created
since backup time if it is needed. Movie-agent can be rolled back to the retained
original image; it has no persistent data volume. Media files were not archived
again or modified by the upgrade process. Normal configured application jobs
continue after restart.

Sources: [Prowlarr release](https://github.com/linuxserver/docker-prowlarr/releases/tag/2.5.2.5491-ls159),
[Bazarr release](https://github.com/linuxserver/docker-bazarr/releases/tag/v1.6.0-ls363),
[Sonarr release](https://github.com/linuxserver/docker-sonarr/releases/tag/4.0.19.2979-ls324),
[Radarr release](https://github.com/linuxserver/docker-radarr/releases/tag/6.3.0.10514-ls316),
[Go release history](https://go.dev/doc/devel/release),
[Huma 2.39.1](https://github.com/danielgtaylor/huma/releases/tag/v2.39.1),
[distroless runtime images](https://github.com/GoogleContainerTools/distroless).


## Greymon reachability recheck — 2026-09-14

Greymon (`192.168.50.30`) and its PiKVM (`192.168.50.31`) are reachable again.
Andromon's neighbor table matches both documented MAC addresses. Ollama responds
from the laptop, andromon, and inside the Open WebUI container. The HTTPS proxy
at `https://ollama.batjaa.site/api/version` returns HTTP 200.

Ollama reports **0.34.0**, matching the
[current stable release](https://github.com/ollama/ollama/releases/tag/v0.34.0)
on this check. Six installed models are visible: `qwen3-coder-64k:latest`,
`qwen3-coder:30b`, `qwen2.5-coder:14b`, `qwen2.5-coder:7b`, `gemma4:e4b`, and
`qwen3.5:0.8b`.

A short `qwen3.5:0.8b` generation initiated inside Open WebUI returned `OK`.
The first request took **44.85 seconds**, with no models loaded beforehand;
a repeat with the model warm took **0.12 seconds**. Ollama's running-model API
reports **624,080,976 bytes**, all in GPU memory, with a 2,048-token context.
These checks verify network access and actual inference; they do not exercise
the full Open WebUI browser chat workflow or the larger installed models.

PiKVM's SSH and HTTPS ports respond, and
`https://kvm.greymon.home.local/` returns HTTP 200. Its authenticated management
functions and software versions have not yet been checked. Greymon's Windows
SSH, RDP, and WinRM probes still time out; Windows and GPU driver update status
therefore remain unverified. ICMP also times out, despite the working Ollama
service, so ping alone must not be used to classify this host as offline.

No software upgrades, host configuration changes, or power actions were made.
