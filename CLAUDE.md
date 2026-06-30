# Project context — home-server

Ansible-based home lab. **Two hosts**, multi-tenant *arr stack + media servers + monitoring + storage with backups.

## Architecture in one diagram

```
                         (public)                          (internal LAN)
              ┌──────────────────┐               ┌────────────────────────┐
   user ────▶ │ Cloudflare DNS   │ ─── 443 ───▶ │  ZenWifi (router)       │
              │ + DDNS auto-IP   │               │  └─ port-fwd 443→.20    │
              └──────────────────┘               │                          │
                                                  │  CRS326 (managed swtch)│
                                                  │   ├── tentomon (.10)    │
                                                  │   │   └─ Pi-hole, DDNS,│
                                                  │   │     cloudflare-dns,│
                                                  │   │     node_exporter   │
                                                  │   ├── andromon (.20)   │
                                                  │   │   └─ everything    │
                                                  │   └── pikvm (.21)       │
                                                  └────────────────────────┘
```

- **andromon**: x86_64 Ubuntu 22.04, runs every container (SWAG, *arr stack, Plex/Jellyfin/Navidrome/PhotoPrism/Nextcloud, monitoring, storage role)
- **tentomon**: Pi 4 arm64, runs network-critical infra (Pi-hole, DDNS) — *not* media. Survives if andromon dies.
- **pikvm**: factory image (not Ansible-managed). Out-of-band recovery.

Inventory groups: `[pi]` and `[homeservers]`. Each play targets one.

## House rules (preferences)

1. **Config-first or it didn't happen.** Every change goes into a role/template/var first, then is applied via the playbook. If you're tempted to `ssh` in and run a one-off, stop:
   - Capture the change in the role
   - Run the playbook with the right tag
   - Verify it's idempotent (run twice, second run is a no-op)
   - Then commit
   The bar: a fresh disaster-recovery rebuild from `_docker_data` + `ansible-playbook main.yml` should reach the same state as the live system. If a setting is only "in the running container's memory" or "saved by clicking Save in a UI", it doesn't survive recovery and isn't real.
   Exceptions: read-only debugging, and the small list of UI-only steps documented in `docs/services.md` (Plex first claim, Bazarr provider auth, etc.). When the UI is the only option, hit the app's REST API from a task (see `roles/containers/monitoring/grafana/tasks/main.yml` setting the org's home dashboard, or `roles/containers/media/navidrome/tasks/main.yml` calling `/auth/createAdmin`).
2. ***arr stack must be automatically configured.** Prowlarr, SABnzbd, Radarr, Sonarr, Whisparr, Bazarr, Seerr, Decluttarr, and related helpers should converge from role defaults, host vars, templates, or small API-driven scripts. Indexers, applications, download clients, root folders, categories, notifications, cleanup rules, and scheduled searches belong in config. Do not leave "go click this in the UI" as the normal path; only document UI steps for true first-claim/auth flows that cannot be driven by an API. Every *arr tag should be safe to rerun and should leave the same state when nothing changed.
3. **Bridge networking, never macvlan.** All containers publish to `127.0.0.1:<port>` on the host; SWAG terminates SSL on `:443` and proxies in. Macvlan is dead — every old role using it has been rewritten.
4. **Hardlinks must work across services.** Bind-mount `/mnt/storage` as `/storage` (or `/media`) inside containers — never individual sub-paths. Radarr, Sonarr, Whisparr, SABnzbd, Plex, Jellyfin, Navidrome all see the same root.
5. **All persistent state goes to `/opt/docker/data/<service>/`.** Media files live under `/mnt/storage/Media/`. Both are backed up.
6. **Secrets in vault, never inline.** `host_vars/<host>/secret.yml` is encrypted; the vault password lives in macOS Keychain via `pass.sh`. To peek: `ansible -m debug -a "var=<key>" <host>`.
7. **Idempotency required.** Re-running any tag is a no-op when state matches.
8. **Hardware acceleration via `/dev/dri` + render/video groups.** When a container needs the iGPU (transcoding, ML), the role should:
   - mount `devices: ["/dev/dri:/dev/dri"]`
   - add the host's `render` (gid 109) + `video` (gid 44) groups via `groups:` or the linuxserver `GIDLIST` env var
   The `gpu_render_gid` and `gpu_video_gid` group_vars are the source of truth — override per host only if the OS uses different gids.

## Adding a new service — minimal checklist

1. New role at `roles/containers/<category>/<name>/{tasks,defaults}/main.yml`
2. Container deployed with `network_mode: bridge` + `published_ports: ["127.0.0.1:<port>:<container_port>"]`
3. SWAG proxy-conf at `roles/network/swag/templates/proxy-confs/<subdomain>.subdomain.conf.j2`
4. Add `enable_<name>: false` default in `group_vars/all/vars.yml` if it should be opt-in
5. Wire role into `main.yml` with `when: enable_<name> | default(false) | bool`
6. **If public:** add to `cloudflare_records:` in `host_vars/tentomon/vars.yml` (CNAME → `ddns.batjaa.site`, `proxied: false`)
7. **Always:** add to `pihole_split_dns:` in `host_vars/tentomon/vars.yml` for internal resolution
8. Bootstrap any admin user via API call (see Navidrome) or env vars (see Nextcloud, Grafana) — not the wizard
9. Document in `docs/services.md`
10. **Add a tile to Homepage** — new entry in `roles/containers/services/homepage/templates/services.yaml.j2` (icon, href, `siteMonitor` on `127.0.0.1:<port>`, optional `widget:` block if Homepage supports the service)
11. **Add a Kuma monitor** at `https://status.batjaa.site` — HTTP check on the SWAG URL with a known-healthy endpoint (`/api/server/ping`, `/health`, `/status.php`, `/`, etc.) and subscribe it to the Postmark notification so failures email out
12. **Add a row to README.md "Services" table** — single line under the right category (Media playback / Photos & files / *arr stack / Monitoring / Network / Storage). The README inventory is the at-a-glance "what does this do" reference; if it's not in the table, future-you will forget the service exists.

## Removing a service — reverse checklist

1. Stop + remove the container (preserve `/opt/docker/data/<name>/` for rollback unless cleanup is explicit)
2. Set `enable_<name>: false` in host_vars; if retirement is permanent, also delete the role + proxy-conf template
3. Drop its entry from `cloudflare_records:` and `pihole_split_dns:` in `host_vars/tentomon/vars.yml`
4. Remove its `*.subdomain.conf` from `/opt/docker/data/swag/nginx/proxy-confs/` on the andromon (Ansible doesn't auto-prune)
5. Remove the Homepage tile from `services.yaml.j2`
6. Pause or delete the Kuma monitor (delete unless you want history)
7. Update `docs/services.md`
8. **Remove the row from README.md "Services" table** — keep the inventory honest

Subdomain conventions favour short names: `music` not `navidrome`, `request` not `seerr`, `photos` not `photoprism`, `kvm` not `pikvm`.

## Storage role is phased

Run order, gated by `storage_phase`:

```
data       (default)         non-destructive: sda → /mnt/sda1, mergerfs cache union
backups    (DESTRUCTIVE)     wipe + format backup1/2/3 (gated by confirm_storage_wipe: true)
pool                          mergerfs union /mnt/backup{1,2,3} → /mnt/backup_pool
mover                         systemd timer 02:00, NVMe cache → sda1 (atime > 7d)
backup                        systemd timer 03:00, /mnt/storage + /opt/docker/data → /mnt/backup_pool with btrfs snapshots
health                        smartd, btrfs scrub timers, textfile collector for Prometheus
all                           every phase
```

Pre-flight: `roles/filesystems/storage/files/verify-readiness.sh` (run by hand before any destructive op).

Drive identity is **pinned by serial** in `host_vars/andromon/vars.yml` under `storage_drives`. UUIDs are also pinned and survive reformatting via `mkfs.btrfs -U`. Never address drives by `/dev/sdX`.

## Disaster recovery

- **Service config restore is fast:** the nightly backup includes `/opt/docker/data/`, written to `/mnt/backup_pool/_docker_data/`. On rebuild: rsync that back + re-run the playbook → containers come up with their existing API keys, libraries, and quality profiles. No wizard click-through.
- **What's *not* automated:** Cloudflare API token, Postmark Server API Token, Plex claim token, friend invites, ZenWifi DHCP/port-forward, Tailscale split-DNS. See `docs/services.md` recovery checklist.
- **What *is* now vault-automated** (was manual at some point): Newshosting + UsenetExpress (`usenet_servers` -> `roles/containers/media/sabnzbd`), NZBgeek + NZBFinder (`prowlarr_indexers` -> `roles/containers/media/prowlarr`).

## Common gotchas

- **Postmark sending from `gmail.com`** triggers Gmail's DMARC and silently drops. Always send from `alerts@batjaa.site` (verified domain).
- **Cloudflare proxied=true on Plex/Jellyfin/PhotoPrism** breaks streaming. Always `proxied: false` on `cloudflare_records:` for media services.
- **SABnzbd hostname check** rejects requests if `Host:` doesn't match its whitelist. Role writes `host_whitelist` to `sabnzbd.ini` automatically.
- **Mergerfs mount option changes** require `umount + mount`, not just a remount.
- **`guid` is per-host:** `1001` on andromon (batjaa), `1000` on tentomon (tentomon). Default `1000` in group_vars is for tentomon. Container PUID/PGID picks it up via `{{ guid }}`.
- **New internal domains often need DHCP/DNS flushing before browser tests.** After adding `pihole_split_dns` or `pihole_local_dns`, renew the client DHCP lease and flush DNS caches before debugging the app/proxy. On macOS: `sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder`, then restart the browser. If the router DHCP DNS changed, reconnect Wi-Fi/Ethernet or renew the lease.
- **LAN DNS must not use public fallback for internal-only names.** DHCP clients should use Pi-hole (`192.168.50.10`) only, not `1.1.1.1`/`8.8.8.8` as secondary. macOS/browser resolvers can accept the public NXDOMAIN first, making internal-only hosts such as `whisparr.batjaa.site` fail even though `dig @192.168.50.10 ...` works.
- **Tailscale split DNS** must be configured for both `home.local` AND `batjaa.site` → Pi-hole at `192.168.50.10`. Without this, browsers can't resolve internal-only subdomains like `grafana.batjaa.site`.
- **Seerr image** (`ghcr.io/seerr-team/seerr:latest`) runs as the `node` user (uid 1000) and ignores PUID/PGID — chown the config dir to `1000:1000` explicitly. Also requires `init: yes` in the docker_container task because the image dropped its init shim.
- **Immich Postgres `command:` overrides** must keep `-c shared_preload_libraries=vchord.so`. The vectorchord extension refuses to load otherwise and immich-server crash-loops on any vector query (SmartSearch, OCR, face detection). The role has a post-deploy guard that fails the play if it drops out — don't disable it.

## Useful commands

```bash
# Run a tag on a host
ansible-playbook main.yml -l andromon --tags="grafana"

# Run a phased role
ansible-playbook main.yml -l andromon --tags="storage" -e storage_phase=backup

# Peek a vault value
ansible -m debug -a "var=cloudflare_dns_token" tentomon

# Edit secrets
ansible-vault edit host_vars/andromon/secret.yml

# SSH (custom port set by geerlingguy.security)
ssh -p 100 batjaa@192.168.50.20      # andromon
ssh tentomon@192.168.50.10            # tentomon (port 22)

# Test SWAG routing without DNS (force resolve via curl)
curl -sk --resolve <sub>.batjaa.site:443:192.168.50.20 https://<sub>.batjaa.site/

# Trigger backup or mover manually
ssh -p 100 batjaa@192.168.50.20 "sudo systemctl start backup-storage.service"
ssh -p 100 batjaa@192.168.50.20 "sudo systemctl start cache-mover.service"
```

## Where to look for things

- `README.md` — host-level setup, network layout, full disaster-recovery flow
- `docs/services.md` — per-service runbook (URLs, vault keys, manual setup steps)
- `docs/storage-ansible-requirements.md` — full storage design + decisions
- `roles/filesystems/storage/files/verify-readiness.sh` — pre-flight check (read-only)
- `group_vars/all/vars.yml` — global defaults, msmtp config, package list
- `host_vars/<host>/vars.yml` — per-host overrides + secrets reference
