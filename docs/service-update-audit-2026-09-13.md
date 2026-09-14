# Home network service update audit — 2026-09-13

> This is the pre-upgrade snapshot. See the [subsequent authorized service upgrades](service-upgrades-2026-09-13.md) for current deployment results.

**You are not fully up to date.** The initial live snapshot contained **99 running containers** across three managed servers: **33 have newer images on their existing tags**, **27 match their current tags**, and **39 are custom builds whose application dependencies were not audited**. Several matching tags are old or unsupported release lines; matching a tag is not a clean bill of health.

During the audit phase, no packages, images, service configuration, or firmware were upgraded. No services were restarted by this audit. Existing uncommitted repository changes were left intact. The audit produced this report and its [sanitized evidence](service-update-audit-2026-09-13.json).

## Scope and method

- Connected over SSH to andromon, tentomon, and wormmon; read every running/stopped container, Docker image metadata, runtime versions, active system/user services, failed units, package candidates, holds, and reboot markers.
- Queried public container registries for the configured tags and matched Linux/CPU architecture. Compared both manifest/index digests and image config digests because the hosts use different Docker image stores. LinuxServer lookups that hit rate limits were retried through its GHCR repositories.
- Checked official release metadata and lifecycle documentation separately from tag freshness. Application versions take precedence over inherited base-image labels: Coolify is 4.1.2, not the serversideup PHP image label 4.4.1; Kroki is 0.31.0, not its Ubuntu label 24.04.
- Host package results use existing APT metadata dated September 12–13, with Docker metadata dated September 11. No `apt update` was run. Counts are pending candidates, not a count of security vulnerabilities.
- Probed TCP ports 22, 50, 80, 100, 443, 8123, 8291, 11434, and 8188 on 192.168.50.1–254. Follow-up used authenticated ASUS/PiKVM APIs and MikroTik neighbor discovery (UDP 5678) from andromon. This is limited discovery, not complete UDP/IPv6/VLAN/IoT firmware coverage.
- Snapshot started around 19:01 PDT. Coolify deployments were occurring during the audit; one Groove container was replaced before its runtime version probe. Container counts and deployed commit IDs describe that initial snapshot.
- No declared container health check was unhealthy, no systemd unit was failed, and no host had a reboot-required marker. Containers without health checks were only confirmed running. Application workflows and backup restores were not tested.

## Highest-priority findings

1. **The MikroTik switch needs a security update.** LAN neighbor discovery from andromon identified the CRS326-24G-2S+ at **192.168.88.1**, advertising **RouterOS 6.49.7 (stable)**. Current v6 stable/long-term is **6.49.21**, released September 3 with the vendor’s security fixes. Updating within v6 addresses the immediate version gap without combining it with a v7 migration. Configuration, management exposure, RouterBOOT, and credentials could not be verified because its management subnet is not reachable by ordinary IP from the audit hosts. No evidence of compromise was established. [Vendor advisory](https://mikrotik.com/supportsec/september-2026-vulnerability/), [v6 changelog](https://mikrotik.com/download/changelogs?versionFilter=6.49).
2. **Both reachable PiKVMs need OS maintenance.** Authenticated web APIs report **KVMD 3.199 / uStreamer 5.37 / Linux 5.15.68-3-rpi-ARCH (2022)** on kvm.andromon, and **KVMD 4.121 / uStreamer 6.41 / Linux 6.12.56-1-rpi (2025)** on kvm.wormmon. The corresponding official repositories currently advertise **KVMD 4.215-1** and **uStreamer 6.66-1** for both architectures. Use the supported full PiKVM OS update path, with physical recovery available; do not update only those two packages. The older andromon KVM may require the documented legacy updater bootstrap. [PiKVM update procedure](https://docs.pikvm.org/_update_os/), [32-bit package versions](https://files.pikvm.org/repos/arch/rpi4/latest/), [64-bit package versions](https://files.pikvm.org/repos/arch/rpi4-aarch64/latest/).
3. **Six MySQL databases are on an end-of-life line.** Fotopass, Tendies, Heyanda, Tech Nomads, TSAS, and Plotling run **8.0.46**. That is the final 8.0 version, but MySQL 8.0 reached EOL in April 2026. Plan and test migration to **8.4 LTS** (current `8.4` image reports **8.4.11**) in each owning application repository, with database backups and restore verification. [MySQL lifecycle](https://dev.mysql.com/doc/relnotes/mysql/8.0/en/).
4. **Tipped is running PostgreSQL 16.4 in a 2024 image.** `postgis/postgis:16-3.4-alpine` still resolves to that exact old image. PostgreSQL 16 is supported, but the current patch is **16.15**. Select and validate a maintained PostgreSQL 16/PostGIS image and extension upgrade procedure; a plain pull of the current tag does nothing. Do not substitute an unverified tag: `16-3.6-alpine` returned 404 during this audit. [PostgreSQL policy](https://www.postgresql.org/support/versioning/), [PostGIS images](https://github.com/postgis/docker-postgis).
5. **The DDNS updater is abandoned.** Tentomon runs `oznu/cloudflare-ddns:latest`, whose arm64 image dates to **2021-03-01**. The repository was archived in 2022. Replace it with a maintained updater, preserving and validating the existing DNS record behavior. `favonia/cloudflare-ddns` is one actively released option to evaluate; its configuration is not a drop-in replacement. [Archived upstream](https://github.com/oznu/docker-cloudflare-ddns), [maintained candidate](https://github.com/favonia/cloudflare-ddns/releases/tag/v1.17.0).
6. **Coolify is 4.1.2; its stable release feed says 4.3.19.** Its helper and realtime sidecars also have supported updates. Update through the Coolify upgrade process as one coordinated stack, then reapply this repo’s Coolify role to restore backup-directory permissions. Sentinel’s standalone GitHub release differs from the Coolify feed; use the version selected by Coolify. [Release feed](https://cdn.coollabs.io/coolify/versions.json), [release notes](https://github.com/coollabsio/coolify/releases/tag/v4.3.19), [local runbook](wormmon-coolify.md).
7. **Jellyfin and Uptime Kuma need planned database migrations.** Jellyfin **10.11.11 → 12.0** includes database changes that require a full backup to roll back. Uptime Kuma **1.23.17 → 2.5.4** requires a data-directory backup and uninterrupted migration, potentially lengthy on the Pi. [Jellyfin upgrade notes](https://github.com/jellyfin/jellyfin/releases/tag/v12.0), [Kuma migration guide](https://github.com/louislam/uptime-kuma/wiki/Migration-From-v1-To-v2).
8. **Immich’s database is behind PostgreSQL patches, even though it matches Immich’s current recommended image.** It runs **14.19**; PostgreSQL 14’s current patch is **14.24**, and support ends **2026-11-12**. The Immich 3.2.0 compose file still pins the same database digest. Resolve through an Immich-compatible database migration, retaining the required VectorChord extensions; do not replace it with stock PostgreSQL. [Current Immich compose](https://github.com/immich-app/immich/releases/download/v3.2.0/docker-compose.yml), [PostgreSQL support dates](https://www.postgresql.org/support/versioning/).

## Managed hosts

| Host | Running containers | Existing-tag image updates | Pending APT candidates | Docker running | Other findings |
|---|---:|---:|---:|---|---|
| andromon | 32 | 24 | 7 | 29.7.2 → 29.8.0 | Ubuntu 22.04.5; kernel 5.15.0-191; node_exporter 1.12.1 current |
| tentomon | 3 | 0 | 27 | Ubuntu-packaged 29.1.3 | Ubuntu 26.04; kernel 7.0.0-1017-raspi; node_exporter 1.11.1 → 1.12.1 |
| wormmon | 64 | 9 | 33 | 29.7.2 → 29.8.0 | Ubuntu 26.04; kernel 7.0.0-31; node_exporter 1.11.1 → 1.12.1 |

Andromon and wormmon also have containerd **2.3.3 → 2.3.5**, Compose **5.4.0 → 5.5.1**, and Buildx **0.36.1 → 0.37.1** available. Schedule Docker host work around the services it may restart. Tentomon uses Ubuntu’s `docker.io` packaging and has no newer Docker candidate in its cached repository metadata; moving to Docker CE would be a packaging change, not an ordinary package update. [Docker release notes](https://docs.docker.com/engine/release-notes/29/).

None of the 67 pending APT candidates had a security-pocket origin in the cached metadata. That does not establish vulnerability-free hosts or containers. No held packages were reported. Package versions and all candidates are captured in the JSON evidence.

Andromon’s running mergerfs binary is **2.33.3**, versus upstream **2.42.0**. The distro version is not a pending APT upgrade. Evaluate this with the storage maintenance plan rather than changing the mounted pool during application updates. [mergerfs release](https://github.com/trapexit/mergerfs/releases/tag/2.42.0).

## Andromon application versions

“Image rebuild” means the application version is unchanged but the published LinuxServer image is newer. These rebuilds can include base-system or dependency fixes.

| Service | Running | Current stable / existing channel | Action |
|---|---|---|---|
| SWAG | 5.8.0-ls484 | 5.8.0-ls484 | Current |
| Prowlarr | 2.5.2.5491-ls158 | 2.5.2.5491-ls159 | Image rebuild |
| Decluttarr | 2.1.0 | 2.1.0 on latest | Matches published latest image |
| Seerr | 3.4.1 | 3.4.1 | Current |
| Bazarr | 1.6.0-ls357 | 1.6.0-ls363 | Image rebuild |
| Whisparr | 3.3.7-release.979 | 3.5.0-release.1585 on v3 | Update |
| Sonarr | 4.0.19.2979-ls321 | 4.0.19.2979-ls324 | Image rebuild |
| Radarr | 6.3.0.10514-ls313 | 6.3.0.10514-ls316 | Image rebuild |
| SABnzbd | 5.1.0-ls266 | 5.1.3-ls273 | Update |
| Beets | 2.13.1-ls346 | 2.14.0-ls352 | Update |
| Tautulli | 2.17.2-ls239 | 2.18.1-ls244 | Update |
| Jellyfin | 10.11.11 | 12.0 | Major migration; backup/plugins/library scan |
| Immich server + ML | 3.1.0 | 3.2.0 | Update together; review compose changes |
| Immich Redis | 6.2.23 | 6.2.24 on 6.2-alpine | Patch available; upstream compose now uses Valkey 9 |
| Plex | 1.43.3.10861 | 1.43.4.10903 | Update |
| movie-agent | Local dev image | No public release comparison | Audit/rebuild from its source and dependency locks |
| Open WebUI | 0.11.0 | 0.11.3 stable | Prefer explicit stable tag; main currently differs from v0.11.3 |
| Paperless | 3.0.5 | 3.1.3 | Update |
| Paperless Redis | 7.4.10 | 7.4.11 | Patch |
| Paperless PostgreSQL | 16.14 | 16.15 | Patch |
| Nextcloud | 34.0.2 | 34.0.4 on stable | Patch; check installed apps |
| Nextcloud MariaDB | 11.8.8 | 11.8.9 on 11 | Patch |
| Home Assistant | 2026.8.1 | 2026.9.2 | Review integration changes, then update |
| Grafana | 13.1.3 | 13.2.1 | Update |
| Prometheus | 3.13.2 | 3.14.0 | Update |
| Navidrome | 0.63.2 | 0.64.0 | Update |
| Stash | 0.31.1 | 0.31.1 | Current |
| Homepage | 1.13.2 | 2.3.0 | Major upgrade; verify YAML and widgets |
| ChartDB | 1.20.1 | 1.20.1 | Current |
| Immich PostgreSQL | 14.19 + VectorChord 0.4.3 | Same Immich image; PostgreSQL 14.24 upstream | Compatibility-bound follow-up; see priority finding |
| cAdvisor | 0.55.1 at gcr.io | 0.60.5 at ghcr.io/google/cadvisor | Change registry and pin release |

cAdvisor’s configured GCR `latest` tag still points to 0.55.1. Upstream now documents GHCR, and `ghcr.io/google/cadvisor:latest` resolves to 0.60.5. The old tag cannot provide that update. [Official repository](https://github.com/google/cadvisor).

Exact current/remote digests and queried registry URLs for every upstream container are in the evidence file. Grafana 13.2.1, Prometheus 3.14.0, and Kuma 2.5.4 version-tag digests were also checked against their rolling tags. Open WebUI `main` did not match the stable v0.11.3 digest; do not treat `main` as a stable-version pin.

PhotoPrism, its MariaDB container, and the old Immich import container are stopped. They are excluded from the 99 running-container count; retirement is consistent with the PhotoPrism repo flag.

## Tentomon services

| Service | Running | Target/status |
|---|---|---|
| Pi-hole | Docker 2026.07.2; Core 6.4.3; Web 6.6; FTL 6.7 | Current image and component versions |
| Uptime Kuma | 1.23.17, pinned to `:1` | 2.5.4; explicit v1 → v2 migration |
| Cloudflare DDNS | oznu image built 2021-03-01 | Archived upstream; replacement needed |
| node_exporter | 1.11.1 | 1.12.1 |

## Wormmon infrastructure and application databases

| Component / consumers | Running | Current compatible target / finding |
|---|---|---|
| Coolify | 4.1.2 | 4.3.19 stable |
| Coolify helper (transient deployment container) | 1.0.14 | 1.0.16 in Coolify feed |
| Coolify realtime | 1.0.16 | 1.0.18 in Coolify feed |
| Coolify Sentinel | 0.0.22 | Matches Coolify feed; reconcile through Coolify upgrade |
| Coolify proxy / Traefik | 3.6.23 | 3.6.25 within current branch; stable upstream 3.7.13 |
| Coolify PostgreSQL | 15.18 | 15.19 |
| Coolify Redis | 7.4.9 | 7.4.11 |
| Fotopass, Tendies, Heyanda, Tech Nomads, TSAS, Plotling MySQL | 8.0.46 | EOL; plan 8.4 LTS migration |
| Heyanda + Plotling Neo4j | 5.26.23 | 5.26.30 on current LTS branch; latest overall 2026.08.1 |
| Otor + Kedge PostgreSQL | 16.14 | 16.15 |
| Groove PostgreSQL | Same 16.14 image as Otor/Kedge in initial snapshot | 16.15; container replaced during audit, so runtime follow-up incomplete |
| Bet-mn + Summit Todo + Demo PostgreSQL | 16.13 | 16.15 |
| Tipped PostgreSQL/PostGIS | PostgreSQL 16.4 on 16-3.4-alpine | Frozen image; select maintained extension-compatible image |
| Kedge Kroki + mermaid/excalidraw/bpmn sidecars | 0.31.0 | 0.32.1; review and update together |
| node_exporter | 1.11.1 | 1.12.1 |

Neo4j 5.26.30 includes dependency security fixes; staying on 5.26 LTS avoids an unnecessary release-track migration. [Official notes](https://neo4j.com/release-notes/database/neo4j-5-26-30/). Kroki’s [0.32.1 release](https://github.com/yuzutech/kroki/releases/tag/v0.32.1) is newer than the pinned 0.31.0 stack. Coolify companion targets above come from the [vendor feed](https://cdn.coollabs.io/coolify/versions.json), not independent “latest” tags.

### Custom application build inventory

These 38 running wormmon containers are built from application repositories, including workers, schedulers, and customized proxy/Grafana images. Image creation dates and commit identifiers do not establish current dependencies or agreement with repository HEAD. Composer/npm/Python dependencies, base-image freshness, and application release status remain unverified; audit in the owning repositories.

| Project | Custom containers | Roles | Build date (UTC) | Deployed commit |
|---|---:|---|---|---|
| bet-mn | 3 | app, scheduler, worker | 2026-06-23 | `ca3cfc7c1bed` |
| demo | 3 | app, scheduler, worker | 2026-05-12 | `71116e226b9c` |
| fotopass | 4 | app, nightwatch, scheduler, worker | 2026-09-14 | `233d4d12860f` |
| groove | 3 | app, scheduler, worker | 2026-09-14 | `0a185bbf2809` |
| heyanda-production | 2 | app, worker | 2026-09-14 | `ec46f0bdff45` |
| kedge | 5 | api, proxy, scheduler, web, worker | 2026-08-25 | `6ffda67e3d7d` |
| otor | 2 | app, grafana | 2026-09-13 | `88ecad7adf44` |
| plotling | 4 | app, nightwatch, worker, worker-secondary | 2026-09-14 | `dc20af764cea` |
| summit-todo | 3 | app, scheduler, worker | 2026-05-21 | `c208b668db58` |
| tech-nomads | 2 | app, nightwatch | 2026-09-14 | `faf238792dea` |
| tendies | 3 | app, nightwatch, worker | 2026-09-14 | `ca4d0d40d6c2` |
| tipped | 3 | app, scheduler, worker | 2026-07-30 | `90fb6d419401` |
| tsas-production | 1 | app | 2026-09-14 | `adfed6e1580b` |

## Network devices and remaining coverage

| Device | Evidence | Update status |
|---|---|---|
| ASUS router, 192.168.50.1 | Authenticated API: ET12, **3.0.0.6.102_37457-gb1775c6_476-gaed2e** | Current published firmware build |
| ASUS AiMesh node, 192.168.50.179 | Authenticated controller: ET12, **3.0.0.6.102_37457-gb1775c6_476-gaed2e** | Current published firmware build; same as main router |
| MikroTik CRS326-24G-2S+, 192.168.88.1 | Neighbor discovery advertises **RouterOS 6.49.7 stable** | Update to **6.49.21** on v6; IP management/RouterBOOT remain unverified |
| kvm.andromon, 192.168.50.21 | Authenticated API: **KVMD 3.199**, **uStreamer 5.37**, 32-bit Linux kernel from 2022 | Behind repository **4.215 / 6.66**; full OS maintenance needed |
| kvm.wormmon, 192.168.50.41 | Authenticated API: **KVMD 4.121**, **uStreamer 6.41**, 64-bit Linux kernel from 2025 | Behind repository **4.215 / 6.66**; full OS maintenance needed |
| greymon, 192.168.50.30 | Router matches documented MAC and marks it **offline**; probes from laptop/andromon fail | Windows, Ollama, GPU drivers, generation tools unverified until reachable |
| kvm.greymon, 192.168.50.31 | Router lists documented MAC as online, but probes from laptop/andromon fail | Conflicting reachability evidence; versions unverified |
| Hue Bridge BSB002, 192.168.50.238 | Unauthenticated API reports firmware **1978293000** | Matches latest published Bridge v2 firmware build; API reports 1.78.0, vendor release label prefixes 1.79 |
| Epson device, 192.168.50.247 | HTTPS server identifies Epson | Exact printer model/firmware unverified |
| Unidentified service, 192.168.50.242:8123 | TCP reachable; `/` and `/api/` return 404 | Product/version unknown; port alone is not proof of Home Assistant |
| Audit workstation, 192.168.50.129 | Local MAC/IP and SSH listener | Outside server audit |

Official device sources: [ASUS ET12 firmware](https://www.asus.com/ca-en/supportonly/zenwifi%20pro%20et12/helpdesk_bios/), [Hue Bridge notes](https://www.philips-hue.com/en-us/support/release-notes/bridge), [PiKVM updating](https://docs.pikvm.org/_update_os/). No-response devices are not proven powered off. Other clients, bulbs, cameras, appliances, devices using different ports, other subnets, and cloud-managed firmware remain outside verified coverage.

### Access references verified in follow-up

The repo contains device addresses but no working management credential references for these devices. 1Password CLI access succeeded; secrets stayed in process memory and were not printed or written into the report.

| Device | 1Password reference / result |
|---|---|
| ASUS ET12 / AiMesh | `Private / ASUS Zen ET12` login works; both nodes queried through the main router |
| PiKVM web APIs | `Private / Homeserver Pikvm` **Login** item works on .21 and .41 |
| PiKVM root/SSH | Separate `Private / Homeserver PiKVM` **Password** item rejected by root SSH on .21 and .41; web credentials and OS credentials are separate |
| MikroTik | `Private / MicroTik router` exists, but its saved URL is **10.0.0.1**; that address serves Ubuntu/OpenSSH rather than the discovered switch. No MikroTik credentials were sent there. Actual advertised management IP is **192.168.88.1** |
| Greymon | No dedicated login found by hostname, Windows device name, or documented IP in 1Password item titles/URLs; machine is currently offline |

Successful ASUS sessions were logged out after the queries. No credentials, 1Password items, router settings, host network addresses, routes, firmware, or device power states were changed. MikroTik’s installed version is discovery-advertised, not authenticated package inventory. PiKVM APIs expose the versions above, but a complete package list and installed update channel remain unavailable without root access.

## Suggested execution order

1. Prioritize the MikroTik security update and PiKVM recovery-path maintenance in separate network/OOB windows. Establish restorable backups for the databases and app configuration affected by upgrades. Schedule production database migrations for the MySQL apps and Tipped first; upgrade Neo4j within its LTS branch.
2. Apply routine same-release patches and image rebuilds through scoped Ansible tags or the owning application’s deployment config. Account for the existing uncommitted Prowlarr/arr-search changes before running those roles.
3. Update Coolify using its supported upgrade workflow and verify scheduled backup-directory access afterward. Update its database and proxy within supported branches.
4. Handle Jellyfin 12, Kuma 2, Homepage 2, and Immich compatibility work individually with release-note review and functional checks. Keep DNS and monitoring changes in separate windows.
5. Replace archived DDNS, migrate cAdvisor to GHCR, and update the two older node_exporter binaries.
6. Apply host package maintenance in separate windows, then verify DNS, ingress, storage mounts, app health, and reboot markers. Treat any Ubuntu release migration for andromon as separate work.
7. Restore switch IP management and PiKVM root access, complete greymon/kvm.greymon and other remaining device checks, and audit custom application dependencies in their owning repositories.
8. Add scheduled read-only update reporting that checks both image digests and supported release tracks. A `latest` tag only advances when the container is pulled/recreated, and an abandoned tag can remain unchanged forever.

No update execution is implied by this report. The audit establishes a concrete maintenance backlog; verified coverage does not yet include every device in the home network.
