# Services Runbook

Quick reference for what's running, where credentials live, and the manual
setup steps that aren't (yet) in Ansible. If you're rebuilding from scratch
and `/mnt/backup_pool/_docker_data/` is intact, skip most of this — see the
"Disaster Recovery from Backup" section in the main README.

## Vault layout

All secrets live in vault-encrypted files. Edit with:

```bash
ansible-vault edit host_vars/tentomon/secret.yml
ansible-vault edit host_vars/andromon/secret.yml
```

Pull a value out for one-off use:

```bash
ansible -m debug -a "var=<key>" <host>
```

## URLs

### Public (Cloudflare CNAME → ddns.batjaa.site)

| URL | Service |
|-----|---------|
| `plex.batjaa.site` | Plex |
| `jellyfin.batjaa.site` | Jellyfin |
| `nextcloud.batjaa.site` | Nextcloud |
| `photos.batjaa.site` | PhotoPrism |
| `request.batjaa.site` | Seerr (friend-facing request UI; successor to Overseerr/Jellyseerr) |

### Internal-only (Pi-hole local DNS, no Cloudflare CNAME)

| URL | Service |
|-----|---------|
| `grafana.batjaa.site` | Grafana |
| `prowlarr.batjaa.site` | Prowlarr |
| `radarr.batjaa.site` | Radarr |
| `sonarr.batjaa.site` | Sonarr |
| `sabnzbd.batjaa.site` | SABnzbd |
| `bazarr.batjaa.site` | Bazarr |

## Networking facts

- ZenWifi forwards public `:443/tcp` → `192.168.50.20:443` (SWAG)
- SWAG terminates SSL and routes by hostname to each container's port on
  `127.0.0.1:<port>`
- Containers see `/mnt/storage` as `/storage` (so `radarr`, `sonarr`,
  `sabnzbd`, `plex`, `jellyfin` all work with hardlinks)
- Tailscale "split DNS" sends `home.local` and `batjaa.site` queries to
  `192.168.50.10` (Pi-hole) so internal browsers resolve correctly

## Order of operations on a fresh build

1. Bootstrap tentomon (README §"Disaster Recovery — tentomon")
2. Bootstrap andromon (README §"Disaster Recovery — andromon")
3. Storage role: `data` → `backups` (with `confirm_storage_wipe: true`)
   → `pool` → `mover` → `backup` → `health`
4. **If you have a `_docker_data` backup**, restore it now (see DR section
   in README) and skip step 5
5. Manual configuration of each service below
6. Verify Cloudflare/Pi-hole DNS (`ansible-playbook ... --tags=cloudflare-dns,pihole`)
7. Verify SWAG cert is fresh and proxy-confs are loaded

---

## Tentomon services

### Pi-hole — `http://pihole.home.local/admin`

**Auth:** `pihole_password` in `host_vars/tentomon/secret.yml`

**Auto-configured:**
- Listening mode (LAN-wide via `FTLCONF_dns_listeningMode=ALL`)
- Local DNS records from `pihole_local_dns` and `pihole_split_dns` in vars
- Upstream resolvers (default Cloudflare/Quad9)

**Manual:**
- Make sure ZenWifi DHCP is pointing clients here as primary DNS
  (`192.168.50.10`), with `1.1.1.1` as secondary fallback
- Enable Tailscale split DNS (admin.tailscale.com → DNS) for `home.local`
  and `batjaa.site` → `192.168.50.10`

### Cloudflare DDNS

**Auth:** `cloudflare_dns_token` in `host_vars/tentomon/secret.yml`

**Auto-configured:** updates `ddns.batjaa.site` to current public IP every
few minutes via the `oznu/cloudflare-ddns` container.

**Manual:** none. The Cloudflare token must have `Zone.DNS:Edit` on the
`batjaa.site` zone. Generate at
https://dash.cloudflare.com/profile/api-tokens.

### Cloudflare DNS records

**Auth:** same `cloudflare_dns_token`.

**Auto-configured:** every entry in `cloudflare_records:` becomes (or
updates) the matching record. Subdomains all CNAME to `ddns.batjaa.site`,
**proxied: false** (Plex etc. break behind Cloudflare's proxy).

---

## Homeserver — base

### SWAG — reverse proxy

**Auth (cert renewal):** `cloudflare_dns_token` (same as DDNS) — used for
DNS-01 wildcard cert challenge. Lives in
`host_vars/andromon/secret.yml`.

**Auto-configured:** wildcard cert for `*.batjaa.site`, all proxy-confs in
`roles/network/swag/templates/proxy-confs/`.

**Manual:** make sure router forwards `:443/tcp` to `192.168.50.20`.

### Postmark (SMTP relay)

**Auth:** `email_postmark_token` in `host_vars/andromon/secret.yml`.

**Manual:**
1. Sign up at https://postmarkapp.com (free tier 100 emails/month)
2. Verify a sender signature for `batjaa0615@gmail.com` (click email link)
3. Copy the **Server API Token** from the server's "API Tokens" tab
4. Paste into vault as `email_postmark_token`

Used by smartd for drive failure alerts and Let's Encrypt for cert
expiry notices.

---

## Storage

See `docs/storage-ansible-requirements.md` for the full design. Quick
facts:

- Drive identity is **pinned by serial** in `host_vars/andromon/vars.yml`
  under `storage_drives`. UUIDs are also pinned and survive reformatting
  via `mkfs.btrfs -U`.
- Pre-flight: `roles/filesystems/storage/files/verify-readiness.sh` reads
  drives by serial, asserts size/model/UUID match, checks SMART. Run it
  before any destructive op.
- Every nightly backup writes `/mnt/backup_pool/_docker_data/` via the
  rsync helper in `backup-storage.sh`. That's what makes service-config
  recovery painless.

---

## Media stack

### Plex — `https://plex.batjaa.site`

**Auth:** `plex_claim_token` (one-time, from https://www.plex.tv/claim/)
in `host_vars/andromon/secret.yml`. Gets the server registered to your
Plex account on first start; not needed afterward.

**Manual setup if `_docker_data` was lost:**
- Sign in with your Plex account
- Add **Movies** library → Folder: `/movies`
- Add **TV Shows** library → Folder: `/tv`
- Add **Music** library → Folder: `/music`
- Settings → Network → **Custom server access URLs**:
  `https://plex.batjaa.site:443`
- Settings → Remote Access → manually specify public port: `443`

### Jellyfin — `https://jellyfin.batjaa.site`

**Auth:** none stored in vault — admin user is created on first-run
wizard.

**Manual setup if `_docker_data` was lost:**
- First-run wizard (admin user, libraries Movies → `/media/Movies`, TV →
  `/media/TV`)
- Disable automatic UPnP port mapping (SWAG handles ingress)
- Per-friend accounts: Dashboard → Users → Add (no email signup)

### Nextcloud — `https://nextcloud.batjaa.site`

**Auth (in vault):**
- `nextcloud_admin_user` (e.g. `batjaa`)
- `nextcloud_admin_password`
- `nextcloud_db_root_password`
- `nextcloud_db_password`

**Auto-configured:** first start uses the admin user/password env vars to
auto-install. Trusted domains include `nextcloud.batjaa.site` and
`nextcloud.home.local`.

**Notes:**
- User data is at `/opt/docker/data/nextcloud-data` (NOT mergerfs — see
  commit history for why)
- File-drop sharing for friends: Files → folder → Share icon → Share
  link → "..." → File drop (upload only)

### PhotoPrism — `https://photos.batjaa.site`

**Auth (in vault):**
- `photoprism_admin_user`
- `photoprism_admin_password`
- `photoprism_db_root_password`
- `photoprism_db_password`

**Manual:**
- After first index, photos classified as NSFW are auto-marked Private.
  Settings → Library → Show Private to review.
- Daily index runs at 01:30 (cron); first index is hours-long for the
  initial photo set.

---

## *arr stack — usenet pipeline

We use **Newshosting** (provider) + **NZBGeek** (indexer). No VPN — usenet
is single-provider SSL.

### SABnzbd — `https://sabnzbd.batjaa.site`

**Manual setup if `_docker_data` was lost:**
- First-run wizard sets API key — **copy it**, you'll need it for
  Prowlarr/Radarr/Sonarr
- Config → Servers → add Newshosting:
  - Host `news.newshosting.com`, Port `563` (SSL), 30 connections
  - User/pass from your Newshosting account
- Config → Categories → add `movies` (folder
  `/storage/usenet/complete/movies`), `tv` (`/storage/usenet/complete/tv`)
- Config → General → host_whitelist already includes
  `sabnzbd.batjaa.site` (Ansible writes this)

### Prowlarr — `https://prowlarr.batjaa.site`

**Manual setup if `_docker_data` was lost:**
- First-run: Forms login (set username/password)
- Indexers → Add → NZBGeek (paste your NZBGeek API key)
- Settings → Apps → add Radarr (`http://radarr:7878`, paste Radarr API key)
- Settings → Apps → add Sonarr (`http://sonarr:8989`, paste Sonarr API key)
- Settings → Download Clients → add SABnzbd
  (`http://sabnzbd:8080`, paste SAB API key, category `movies`)
- Click Apps → Sync App Indexers (pushes indexer config to all *arr apps)

### Radarr — `https://radarr.batjaa.site`

**Manual setup if `_docker_data` was lost:**
- First-run: Forms login
- Settings → Media Management → Root Folders → `/storage/Media/Movies`
- Settings → Download Clients → SABnzbd (`sabnzbd:8080`, category `movies`)
- Settings → Connect → Plex Media Server: host `192.168.50.20`, port
  `32400`, paste Plex token (Settings → Account → "X-Plex-Token" in URL
  of any settings page)
- Settings → Connect → Jellyfin: `192.168.50.20:8096`, Jellyfin API key
- Quality profile: HD-1080p (or larger if you have the storage budget)

### Sonarr — `https://sonarr.batjaa.site`

Same shape as Radarr but for TV.

- Root folder: `/storage/Media/TV`
- Download client category: `tv`
- Quality profile: HD-1080p
- Same Plex/Jellyfin connect entries

### Bazarr — `https://bazarr.batjaa.site`

**Manual setup:**
- Settings → Languages → add language profile `English`
- Settings → Radarr → host `radarr`, port `7878`, API key
- Settings → Sonarr → host `sonarr`, port `8989`, API key
- Settings → Providers → add OpenSubtitles.com (free signup) and any
  others you want

### Seerr — `https://request.batjaa.site` (public)

Successor to Overseerr + Jellyseerr (unified). Image:
`ghcr.io/seerr-team/seerr:latest`. Auto-migrates from an existing
Overseerr config dir on first start.

**Auto-configured by Ansible (role API tasks, need `seerr_api_key` in vault):**
- Plex libraries enabled (Movies + TV Shows) — required or availability-sync
  has nothing to scan and every request stays "processing" forever
- Radarr/Sonarr availability scans (`syncEnabled`)
- `applicationUrl`, `trustProxy`, `csrfProtection`
- Email notifications via Postmark from `alerts@batjaa.site`

Role gotchas (cost a debugging session, don't rediscover):
- `GET /settings/plex/library?sync=true` rediscovers libraries but **resets
  their enabled flags** — only call it when the library list is empty
- `PUT /settings/{radarr,sonarr}/:id` rejects bodies containing the
  read-only `id` field (400)
- `seerr_api_key` in the vault must match `main.apiKey` in
  `/opt/docker/data/seerr/settings.json` — Seerr regenerates it on
  migrations; a stale key 403s every role task

**Manual setup if `_docker_data` was lost:**
- Sign in with your Plex (and/or Jellyfin) account → it auto-discovers
- Settings → Services → Radarr: host `radarr`, port `7878`, API key,
  quality profile, root folder `/storage/Media/Movies`, external URL
  `https://radarr.batjaa.site`
- Settings → Services → Sonarr: host `sonarr`, port `8989`, API key,
  root folder `/storage/Media/TV`
- Settings → Users → Default Permissions: tick "Auto-Approve" if you
  want friends' requests to skip your inbox
- Copy the regenerated API key into the vault (`seerr_api_key`), then
  re-run `--tags seerr` to apply everything in the auto-configured list

### arr-search (timer, no UI)

`arr-missing-search.timer` on andromon, nightly 04:00: triggers
`MissingMoviesSearch` in Radarr, then `MissingEpisodeSearch` in Sonarr
30 minutes later. Exists because RSS sync only grabs releases posted
*after* the initial search — without it, a request whose first search
found nothing is never retried. Logs: `journalctl -u arr-missing-search`.

### immich-monitor (timer, no UI)

`immich-textfile.timer` on andromon, every 10 min (role `monitoring/immich`,
tag `immich-monitor`): hits Immich's `/api/jobs`, writes `immich_queue_paused`
and `immich_queue_jobs{queue,state}` to the node_exporter textfile collector,
and **emails `alerts@batjaa.site` → me when `thumbnailGeneration` is left
paused** (debounced via `/var/lib/immich-monitor/alert-state.json`).

Exists because of a past failure: one corrupt JPEG segfaulted libvips inside
immich-server, BullMQ retried it nightly → crash-loop. The stopgap was pausing
the whole thumbnail queue from the UI, which is invisible to the Kuma HTTP
check (server stays "healthy") — so thumbnails silently stopped for every new
upload for weeks. This collector makes the paused state observable + alertable.
If it fires: look for a poison asset (probe missing-thumbnail originals through
sharp, one subprocess each; exit 139 = the culprit), delete it via
`DELETE /api/assets {force:true}`, then resume with
`PUT /api/jobs/thumbnailGeneration {command:'resume'}`. Logs:
`journalctl -u immich-textfile`.

### Plex ↔ *arr library updates

inotify does not propagate through the mergerfs union, so Plex can never
see new files on its own. Two config-managed mechanisms cover it:
- Radarr/Sonarr "Plex (Ansible)" connect notification → partial scan of
  the imported path within seconds (mapFrom/mapTo translate
  `/storage/Media/*` to Plex's `/movies` + `/tv` mounts)
- Plex hourly scheduled scan (`/:/prefs` set by the plex role) as backstop

---

## AI / agents

### Ollama — `https://ollama.batjaa.site`

**Where it runs:** `greymon` (Windows, not Ansible-managed)

**Manual requirements on greymon:**
- Start Ollama bound to the LAN, not only localhost.
- Ensure Windows Firewall allows `11434/tcp` from the local network.
- Pull at least one coding-oriented model before testing Open WebUI.

This repo assumes Ollama is reachable at `http://192.168.50.30:11434`.

### Open WebUI — `https://chat.batjaa.site`

**Where it runs:** `andromon`

**Auto-configured by Ansible:**
- Docker container on `127.0.0.1:3014`
- SWAG reverse proxy for `chat.batjaa.site`
- upstream Ollama URL set to `greymon`
- shared `agents-net` Docker network for tool servers

**Manual first-run:**
- Visit `https://chat.batjaa.site`
- Create the first account; it becomes admin
- Confirm the Ollama connection works
- Select a model and run a smoke test prompt

**Important behavior:**
- Tool registrations seeded by `TOOL_SERVER_CONNECTIONS` only reliably apply
  on first boot with a fresh Open WebUI data dir
- Later tool changes should be done via API/bootstrap automation or by
  recreating the Open WebUI data volume

### Tool agents

The long-term pattern here is:

- Open WebUI for chat and tool orchestration
- one small container per service-domain integration

Current scaffold:

- `movie-agent` on the `agents-net` Docker network

For the architecture and rollout strategy, see
[`docs/agents.md`](/Users/batjaa/git/home-server/docs/agents.md).

### Hermes — `https://agent.batjaa.site` (autonomous coding agent)

Different from the tool agents above: Hermes is asynchronous and autonomous —
you hand it work over Telegram (or via the vault), it grinds in the background and
opens PRs. Full architecture in
[`docs/hermes-agent.md`](/Users/batjaa/git/home-server/docs/hermes-agent.md).

- Image `nousresearch/hermes-agent:latest`, runs on `andromon`, state in
  `/opt/docker/data/hermes/` (backed up). Model backend = greymon Ollama.
- Dashboard on `agent.batjaa.site` (internal-only, HTTP basic auth).
- Opt-in: `enable_hermes: true` in `host_vars/andromon/vars.yml`.

Vault secrets in `host_vars/andromon/secret.yml`:

```yaml
hermes_github_token: "<fine-grained PAT, bot account, contents:write on feature branches + PR rw>"
hermes_telegram_bot_token: "<from @BotFather>"
hermes_dashboard_password: "<basic-auth pw>"
hermes_api_server_key: "<openssl rand -hex 32>"
```

Manual setup steps (UI/CLI-only, not yet automated):

- One-time interactive setup writes `~/.hermes/config.yaml`:
  `docker run -it --rm -v /opt/docker/data/hermes:/opt/data nousresearch/hermes-agent setup`
- GitHub: create the bot account + fine-grained token, enable branch protection on
  `main` of each target repo (require PR + CI + review).
- Telegram: create the bot via @BotFather; confirm the allowed-users schema.
- The host Docker socket is intentionally **not** mounted — see the security note
  in `docs/hermes-agent.md` before changing the sandbox execution model.

---

## Monitoring

### Prometheus — `127.0.0.1:9090` (andromon-internal only)

Scrapes:
- `localhost:9090` (self)
- `192.168.50.20:9100` (andromon node_exporter)
- `192.168.50.10:9100` (tentomon node_exporter)
- `cadvisor:8080` (container metrics)
- node_exporter textfile collector picks up `/var/lib/node_exporter/textfile_collector/storage.prom` (SMART, btrfs scrub, backup status)

Edit scrape jobs in `host_vars/andromon/vars.yml` →
`prometheus_scrape_jobs`.

### Grafana — `https://grafana.batjaa.site`

**Auth:**
- `grafana_admin_user` (e.g. `batjaa`)
- `grafana_admin_password`

**Auto-configured dashboards** (provisioned, read-only):
- Home (overview — set as default home page)
- Node Exporter Full
- Cadvisor exporter
- Storage Health

Adding new dashboards: drop a JSON file in
`roles/containers/monitoring/grafana/files/provisioning/dashboards/` and
re-run `--tags=grafana`.

---

## PiKVM — `kvm.<host>.home.local` (internal only, self-signed cert)

Out-of-band recovery for physical machines — useful when a host has no
network or won't boot. One PiKVM per managed box, named after its target:

| URL | IP | Attached to |
|---|---|---|
| `https://kvm.andromon.home.local` | `192.168.50.21` | `andromon` (media + *arr stack) |
| `https://kvm.greymon.home.local` | `192.168.50.31` | `greymon` (LLM/gaming PC) |
| `https://kvm.wormmon.home.local` | `192.168.50.41` | `wormmon` (planned web host) |

**Auth:** username/password set during PiKVM image flashing (separate
device, not Ansible-managed). Defaults to `admin/admin` if untouched.

**Cert:** the device serves its own self-signed cert; browser warns on
first visit. Accept and save the exception.

**Hardware setup:**
- HDMI from andromon → PiKVM HDMI in
- USB from PiKVM → andromon USB (acts as keyboard + storage emulation)
- Ethernet on the LAN

**Use cases:**
- BIOS / boot menu access from a browser
- Console attach if SSH is dead
- Emulate a USB drive to install OS without physical access
- Power button via the optional ATX board

The PiKVM stays on its own factory image — we don't manage it via Ansible.
Backups: any custom config you do via the web UI lives on the SD card and
is not backed up here.

## Manual config NOT in Ansible (recovery checklist)

If you're rebuilding and `_docker_data` is also gone, these are the
human-only steps you'll have to redo:

- [ ] Cloudflare API token (regenerate, paste into both vaults)
- [ ] Postmark Server API Token (regenerate, paste into andromon vault)
- [ ] Plex claim token (one-time, from plex.tv/claim)
- [ ] NZBGeek API key (from your NZBGeek account → API Key)
- [ ] Newshosting username + password
- [ ] OpenSubtitles.com signup + Bazarr provider config
- [ ] Plex friend invites (re-share library, re-invite emails)
- [ ] Jellyfin friend accounts (recreate on Dashboard → Users)
- [ ] ZenWifi router config:
  - DHCP reservations for tentomon (`.10`), andromon (`.20`),
    pikvm (`.21`)
  - DNS server → `192.168.50.10`
  - Port forward `:443/tcp` → `192.168.50.20`
- [ ] Tailscale split-DNS for `home.local` + `batjaa.site` →
  `192.168.50.10`
- [ ] Cloudflare apex `batjaa.site` A record + MX records (Hover) — these
  predate our `cloudflare_records:` and aren't tracked
