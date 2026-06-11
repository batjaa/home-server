# Seerr request hardening plan

*Diagnosed 2026-06-11 against the live system (Seerr/Radarr/Sonarr/Prowlarr/SAB/Plex APIs).*

## Symptom

96 requests in Seerr since 2026-04-27: **94 stuck "processing", 0 ever reached
"available"** — including titles that downloaded and imported within hours
(Chernobyl, Batman Begins are in Plex). To a requester, every request looks
like it failed forever.

## Diagnosis — two separate problems

### A. The availability feedback loop is completely disconnected

Downloads work (SAB at 11 MB/s, 10 items in queue, Sonarr imported 252
episodes through 2026-06-10). But nothing downstream ever learns about it:

| # | Break | Evidence |
|---|-------|----------|
| 1 | Seerr's Plex connection has **no libraries enabled** | `GET /api/v1/settings/plex` → `"libraries": []` — the `plex-recently-added-scan` / `plex-full-scan` jobs scan nothing |
| 2 | Seerr's Radarr & Sonarr scans disabled | both services have `"syncEnabled": false` — no fallback availability detection |
| 3 | **Plex never scans its own libraries** | `/:/prefs`: `FSEventLibraryUpdatesEnabled=false`, `FSEventLibraryPartialScanEnabled=false`, `ScheduledLibraryUpdatesEnabled=false`. Last scan + last `addedAt`: **2026-05-08**. Everything imported since is on disk but invisible in Plex |
| 4 | Radarr/Sonarr have **zero connect notifications** | `GET /api/v3/notification` → `[]` on both — imports never trigger a Plex refresh |

Note: even if Plex FSEvents were enabled, **inotify does not propagate
through the mergerfs union** at `/mnt/storage`, so filesystem watching can
never work here. The *arr → Plex connect notification is the only reliable
real-time trigger; scheduled scans are the backstop.

### B. A real fraction of requests genuinely never download

- Radarr: 73 movies, only 33 have files — **40 monitored, missing**
- Sonarr: **438 missing episodes**, queue empty
- 17 `downloadFailed` in Radarr history, all *"Aborted, cannot be completed"*
  (incomplete/DMCA'd usenet posts)
- Only **2 indexers** (NZBgeek, NZBFinder), usenet-only, no torrent fallback.
  Hard-to-find content (old kids' shows: Little Bear, Daniel Tiger,
  Numberblocks…) has aged off usenet
- Nothing re-searches the missing backlog: RSS sync only catches *new* posts;
  a failed initial search is never retried

### C. Hardening gaps found along the way

- Vault `seerr_api_key` is **stale** — live Seerr returns 403 for it (key was
  likely regenerated during the Overseerr→Seerr migration). I authenticated
  via `plex_token` instead
- Seerr `applicationUrl` empty (notification links would be broken),
  CSRF off, `trustProxy` unset
- No notification agents in Seerr — users never hear about
  approved/available/failed, admin never hears about failures
- Updates pending: Radarr 6.1.1→6.2.1, Prowlarr 2.3→2.4, Seerr update flag set
- SSH/ICMP to andromon was unreachable from this Mac (`no route to host` on
  port 100) while HTTPS through SWAG worked — verify where the playbook can
  run from before starting

---

## Phase 1 — Reconnect the feedback loop (fixes ~50+ of the 94 instantly)

All config-first, per house rules. Each step is an idempotent API task in the
owning role (Navidrome/Grafana pattern).

1. **Plex role: enable scheduled library scans.**
   Task: `PUT /:/prefs?ScheduledLibraryUpdatesEnabled=1&ScheduledLibraryUpdateInterval=3600`
   (token from vault `plex_token`). Hourly is cheap; it is the backstop, not
   the primary trigger. Leave FSEvents off — useless over mergerfs.

2. **Radarr + Sonarr roles: add a "Plex Media Server" connect notification.**
   `POST /api/v3/notification` (check-by-name first for idempotency) with
   `onDownload/onUpgrade: true`, implementation `PlexServer`, fields:
   host `192.168.50.20` (Plex is not on `arr-net`), port `32400`,
   `authToken: {{ plex_token }}`, `updateLibrary: true`.
   → Plex refreshes the exact path within seconds of every import.

3. **Seerr role: enable Plex libraries + arr sync.**
   Using the (refreshed, see Phase 3) API key:
   - enable Movies (2) + TV (3) libraries: `GET /api/v1/settings/plex/library?enable=2,3`
   - `PUT /api/v1/settings/radarr/0` and `/sonarr/0` with `syncEnabled: true`
     (send back the full object with the flag flipped)

4. **One-time backlog flush:** trigger `plex-full-scan`, then
   `availability-sync` via `POST /api/v1/settings/jobs/<id>/run`. Expect the
   ~50 already-imported requests to flip to AVAILABLE.

**Verify:** `GET /api/v1/request/count` shows `available > 0`; Plex
`recentlyAdded` shows post-May-8 titles.

## Phase 2 — Raise the fulfillment rate (the other ~40)

5. **Backlog re-search timer.** New systemd timer (storage-role mover/backup
   pattern) on andromon, e.g. nightly at 04:00, staggered:
   `POST /api/v3/command {"name":"MissingMoviesSearch"}` to Radarr and
   `{"name":"MissingEpisodeSearch"}` to Sonarr. Respect indexer API limits —
   nightly, not hourly. (Alternative: a Huntarr role does this with
   rate-limiting built in; start with the timer — no new app.)

6. **Broaden indexers.** Add 1–2 more Newznab indexers to `prowlarr_indexers`
   in the vault (e.g. DrunkenSlug, NinjaCentral, abNZB). The role already
   syncs them non-destructively.

7. **Optional, biggest reach gain: torrent fallback** for content that aged
   off usenet — qBittorrent role (bridge, `127.0.0.1` publish, `/storage`
   bind for hardlinks) + 1–2 trackers in Prowlarr with lower priority than
   usenet. Most of the 438 missing kid-show episodes will only ever come
   from torrents. Defer if not worth the seeding/VPN considerations.

8. **Leave decluttarr alone** — it's already removing failed/stalled items,
   and Radarr `autoRedownloadFailed=true` is verified working (failed
   Sex-and-the-City grabs were retried with alternates until one imported).

**Verify:** missing counts trend down over a week; request an old/obscure
title and watch it fulfill.

## Phase 3 — Seerr hardening + visibility

9. **Rotate the vault key:** copy the live `apiKey` from
   `/opt/docker/data/seerr/settings.json` into `seerr_api_key`
   (`ansible-vault edit host_vars/andromon/secret.yml`) so role automation
   works again. (Or regenerate in Seerr UI, then vault the new one.)

10. **Seerr main settings task:** `applicationUrl: https://request.batjaa.site`,
    `trustProxy: true` (behind SWAG, makes user IPs in logs real),
    `csrfProtection: true` (safe: site is HTTPS-only; API-key access is exempt).

11. **Seerr email notifications via Postmark:** SMTP
    `smtp.postmarkapp.com:587`, user/pass = `email_postmark_token`, sender
    **`alerts@batjaa.site`** (never gmail.com — DMARC gotcha). Enable:
    request approved / available / failed for users; new-request + failed for
    admin. Templated into the role via the API like the rest.

12. **Kuma monitor sanity:** keep the existing HTTP check on
    `https://request.batjaa.site` but point it at `/api/v1/status` with a
    keyword check on `"version"`.

13. **Update images:** re-run the playbook with `--tags radarr,prowlarr,seerr`
    (roles already `pull: yes`) to clear the update warnings.

## Phase 4 — End-to-end verification

- Request a fresh, easily-available movie in Seerr. Within ~1h, with no
  manual steps: grabbed → SAB → imported → Plex connect refresh → request
  flips **AVAILABLE** → notification email arrives.
- Re-run each touched tag twice; second run is a no-op (house rule 6).
- Update `docs/services.md` (Seerr section: libraries/sync/notifications now
  Ansible-managed; remove them from manual-setup list).

## Expected outcome

| Metric | Before | After |
|--------|--------|-------|
| Requests ever reaching AVAILABLE | 0 / 96 | ~55 immediately, rising as backlog searches run |
| Plex freshness | last scan 34 days ago | seconds after import |
| User feedback on requests | none | email on approved/available/failed |
| Missing backlog retry | never | nightly |
