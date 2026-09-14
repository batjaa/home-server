# Wormmon / Coolify Plan

`wormmon` is the dedicated app hosting box for side projects.

## Purpose

- Admin UI: `deploy.batjaa.site`
- Hostname on the LAN: `wormmon.home.local`
- Production apps: custom public domains such as `plotling.app`
- Preview apps: `<app>.preview.batjaa.site`, for example `plotling.preview.batjaa.site`

## Host baseline

- OS: Ubuntu Server LTS
- IP: `192.168.50.40`
- Runtime: Docker
- Persistent state: `/opt/docker/data`
- Storage: single local NVMe only
- Access: SSH on port `22`, Tailscale for administration
- Monitoring: `node_exporter` scraped by Prometheus on `andromon`

This host should stay single-purpose. Do not install host-level PHP, Go,
nginx, MySQL, Postgres, or Redis outside containers.

## DNS model

### batjaa.site

- `deploy.batjaa.site` is a Cloudflare-managed CNAME to `ddns.batjaa.site`
- `*.preview.batjaa.site` is a Cloudflare-managed wildcard CNAME to
  `ddns.batjaa.site`

### Internal DNS

- `wormmon.home.local -> 192.168.50.40`
- `deploy.batjaa.site -> 192.168.50.20` via Pi-hole split DNS
- `*.preview.batjaa.site -> 192.168.50.20` via Pi-hole wildcard dnsmasq rule

### App-owned public domains

Each production app keeps its own DNS in its own zone, for example:

- `plotling.app -> your home public ingress`
- `www.plotling.app -> your home public ingress`

Those records are not managed from this repo unless the zone is also moved
under the same Cloudflare account and intentionally added here.

## Ingress model

Use `andromon` as the only public edge. `wormmon` stays an internal app host.

- Router forwards `80/tcp` and `443/tcp` to `192.168.50.20`
- SWAG on `andromon` terminates public TLS and proxies selected hostnames to `wormmon`
- `wormmon` stays on the LAN; it does not receive the public port-forward directly

This avoids breaking the existing public services on `andromon` while still
letting `wormmon` host app workloads.

## Coolify deployment model

Use Coolify projects/environments with Docker-first deploys.

- Default deploy style: Docker Compose
- Each app gets its own project/environment variables
- Each app gets its own database service when it needs one
- Each preview environment gets its own isolated services when writes matter

Typical layouts:

### Stateless Go app

- `app`
- optional `redis`

### PHP app

- `nginx`
- `php-fpm`
- `db`
- optional `redis`
- optional `worker`

### Full app stack via Compose

Use Compose when the repo naturally owns app + worker + db + redis together.
That should be the default for side projects unless the app is trivially
single-container.

## Database guidance

Prefer one database per app, not one shared server-wide database namespace.

- One Postgres/MySQL service per app is the default
- Separate preview DBs from production DBs
- Redis should also be app-local unless there is a strong reason to share it

This keeps blast radius small and makes app removal straightforward.

## Filesystem guidance

- Keep Coolify and app state under `/opt/docker/data`
- Use local Docker volumes or bind mounts under `/opt/docker/data/<service>`
- Do not mount `/mnt/storage` from `andromon`
- Treat the single NVMe as the app box's working storage until backup policy is added

## What this repo manages now

- `host_vars/wormmon/vars.yml` defines the host baseline
- `roles/containers/services/coolify` installs and starts the Coolify control plane
- `host_vars/tentomon/vars.yml` manages:
  - `wormmon.home.local`
  - `deploy.batjaa.site`
  - wildcard `*.preview.batjaa.site`
- `host_vars/andromon/vars.yml` scrapes `wormmon` node exporter
- `roles/network/swag/templates/proxy-confs/deploy.subdomain.conf.j2` proxies
  `deploy.batjaa.site` to `wormmon`
- `roles/network/swag/templates/proxy-confs/preview.subdomain.conf.j2` proxies
  `*.preview.batjaa.site` to `wormmon`

## Optional bootstrap secrets

If you want Ansible to create the first Coolify admin account during install,
set these in `host_vars/wormmon/secret.yml`:

- `coolify_root_username`
- `coolify_root_user_email`
- `coolify_root_user_password`

If they are left unset, Coolify will present the normal first-run registration
page on first visit to `http://wormmon:8000` or later through
`deploy.batjaa.site`.

## Next implementation steps

1. Add `wormmon` to inventory with `ansible_host=192.168.50.40`
2. Create `host_vars/wormmon/secret.yml`
3. Bootstrap Ubuntu, SSH key auth, and passwordless sudo
4. Run `ansible-playbook main.yml -l wormmon`
5. Run the `coolify` role on `wormmon`
6. Leave router `80/443` forwarded to `andromon`
7. Point `deploy.batjaa.site` and app domains at the existing public edge
8. Create the Coolify admin account immediately on first visit
9. Create the first project with its own DB service

## App deployment pattern

For each new public app domain:

1. Create the public DNS record for the domain so it points at your home edge.
2. Ensure SWAG has certificate coverage for that domain.
3. Add an SWAG proxy-conf on `andromon` for the domain.
4. Proxy the request to `wormmon`, preserving the original `Host` header.
5. In Coolify, configure the application to answer that same hostname.

Example production app:

- Public DNS: `plotling.app -> ddns/public IP`
- SWAG on `andromon`: `server_name plotling.app www.plotling.app`
- Upstream target: `wormmon:80` or `wormmon:443`, depending on the Coolify proxy path you use
- Coolify app domain: `plotling.app`

Example preview app:

- Public DNS: `*.preview.batjaa.site -> ddns/public IP`
- SWAG on `andromon`: wildcard/regex proxy to `wormmon:80`
- Coolify preview domain: `plotling.preview.batjaa.site`

## Plotling production domain (`plotling.app`)

Plotling runs in Coolify application `g111a4eausi813qdtzbsgfrf`, project
`xv5yhvdunkvljj16nyvh32wg`, production environment, from `batjaa/plotling` `main`.
Its Compose stack includes the web app, two queue workers, MySQL, Neo4j, and
Nightwatch. R2 remains the media store. Coolify's application stop grace period
is 960 seconds to allow long story jobs to finish; its default 30 seconds
overrides Compose's grace period during deployments. Workers use SIGTERM and
a container init process for graceful shutdown.

The route is Cloudflare → andromon/SWAG → wormmon/Traefik → Plotling. The
`plotling-app.subdomain.conf.j2` template handles the apex and redirects `www`.
The local host variables declare apex and `www` CNAMEs, split DNS, and certificate
coverage for both names. Those host-variable files remain gitignored by this
repository's existing convention. Apply with the `swag`, `cloudflare-dns`, and
`pihole` tags; `/up` is the application's health endpoint.

The migration source is `/home/forge/plotling.app/current` on `tinker-box`.
Preserve its maintenance mode and disabled worker after cutover. Its database is
then a rollback snapshot, not an active replica; copy new writes back before any
rollback. Production keeps the same app key and service credentials.

## Groove production domain (`usegroove.app`)

`usegroove.app` is a Cloudflare Registrar zone routed through the existing
home ingress:

```text
visitor -> Cloudflare -> andromon/SWAG -> wormmon/Traefik -> Groove app
```

The desired state is split across the app and home-server repositories:

- `home-server/host_vars/tentomon/vars.yml` declares proxied CNAMEs for the
  apex and `www`, plus Pi-hole split-DNS records.
- `home-server/host_vars/andromon/vars.yml` adds both names to the SWAG
  certificate SAN list.
- `home-server/roles/network/swag/templates/proxy-confs/usegroove-app.subdomain.conf.j2`
  proxies the apex to Coolify and permanently redirects `www` to the apex.
- `groove/.batjaa/app.yml` records the production domains alongside the
  existing preview domain.
- Coolify routes `https://usegroove.app` to the Compose `app` service. It does
  not need `www.usegroove.app` because SWAG handles that redirect.

### Cloudflare prerequisites

The `cloudflare_dns_token` in `host_vars/tentomon/secret.yml` is also mounted
into SWAG for DNS-01 certificate issuance. It must have **Zone DNS Edit** for
both `batjaa.site` and `usegroove.app`; a token restricted to only the original
zone will create neither the records nor the certificate.

Because the `usegroove.app` records are proxied, set the zone's **SSL/TLS
encryption mode** to **Full (strict)** in Cloudflare. SWAG presents a public
Let's Encrypt certificate matching the apex and `www`, so strict validation is
supported. This setting is intentionally a one-time dashboard step because the
DNS-only API token has no Zone Settings permission.

### Apply or recover the route

Run DNS and split DNS first, then issue the expanded origin certificate and
install the nginx vhost:

```bash
cd ~/git/home-server
ansible-playbook main.yml -l tentomon --tags="cloudflare-dns,pihole"
ansible-playbook main.yml -l andromon --tags="swag"
```

In Coolify, open the Groove application and set the Compose `app` service's
domains to:

```text
https://groove.preview.batjaa.site,https://usegroove.app
```

Keep the preview hostname during the transition so released mobile builds and
existing NFC stickers continue to work. Set the non-preview application
environment variables to:

```dotenv
APP_URL=https://usegroove.app
SANCTUM_STATEFUL_DOMAINS=usegroove.app,groove.preview.batjaa.site
```

Redeploy Groove after changing either the domains or environment variables.

### Verify

```bash
dig +short usegroove.app @1.1.1.1
curl -I https://usegroove.app
curl -I https://www.usegroove.app
curl -fsS https://usegroove.app/up
```

Expected results:

- the public lookup returns Cloudflare anycast addresses;
- the apex returns the Groove application over HTTPS;
- `www` returns `308` with `Location: https://usegroove.app/...`;
- `/up` returns a successful response.

## Certificate notes

`*.batjaa.site` does not cover `*.preview.batjaa.site` — wildcards only
match one label deep (RFC 6125). SWAG needs explicit additional coverage
for **`*.preview.batjaa.site` only**, not `preview.batjaa.site`, since
that single-label name is already covered by `*.batjaa.site`. Let's
Encrypt rejects requests that include both a single-label name and its
parent wildcard ("redundant with a wildcard domain in the same request").

The knob is `swag_extra_domains` in `host_vars/andromon/vars.yml`:

```yaml
swag_extra_domains: "*.preview.{{ host }},kedge.page,usegroove.app,www.usegroove.app"
```

For third-party app domains like `plotling.app`, add those domains to
`swag_extra_domains` (comma-separated) before exposing them publicly.

### Certificate re-issue after changing `swag_extra_domains`

The SWAG role compares the live certificate SANs with `swag_extra_domains` and
automatically runs certbot when a name is missing. Normally, re-running the
role is enough. If that task fails and you need to reproduce it manually:

```bash
ssh -p 100 batjaa@192.168.50.20 'docker exec swag certbot certonly \
  --config-dir /config/etc/letsencrypt \
  --work-dir /tmp/letsencrypt \
  --logs-dir /config/log/letsencrypt \
  --non-interactive --agree-tos --expand --force-renewal \
  --authenticator dns-cloudflare \
  --dns-cloudflare-credentials /config/dns-conf/cloudflare.ini \
  --cert-name batjaa.site \
  -d batjaa.site -d "*.batjaa.site" -d "*.preview.batjaa.site" \
  -d kedge.page -d usegroove.app -d www.usegroove.app \
  --preferred-challenges dns-01'
ssh -p 100 batjaa@192.168.50.20 'docker exec swag nginx -s reload'
```

The `--config-dir` flags are mandatory — inside this SWAG image
`/etc/letsencrypt/` is a real directory, separate from the persistent
`/config/etc/letsencrypt/`. Nginx reads from `/config/keys/cert.crt`
(symlinks through `/config/etc/letsencrypt/`); without the explicit
dirs, certbot writes to the ephemeral path and the cert is never served.

---

## Deploying a new preview app

End-to-end happy path via [`batjaa/app-bootstrap`](https://github.com/batjaa/app-bootstrap):

```bash
new-app demo2 --yes
```

That chains five steps. To deploy `demo2` (or any name) manually, run
them individually — useful when something errors mid-pipeline and you
want to resume from where it broke:

### 1. Scaffold Laravel

```bash
new-laravel demo2
# Vue 3 SPA + Sanctum default. --frontend inertia|blade, --nova, --cashier,
# --no-social to customize.
```

Creates `~/git/demo2`, installs Laravel, wires Vite + Pinia + vue-router,
patches `bootstrap/app.php` with `trustProxies(at: "*")` so generated apps
work behind SWAG → Traefik.

### 2. Write the deployment manifest

```bash
new-app-config demo2
# preview-only; add --domain plotling.app --www for production.
```

Drops `.batjaa/app.yml` (name, repo, framework, domains, db, deploy port).
Consumed by `new-wormmon-app`.

### 3. Generate Coolify-ready Docker files

```bash
new-laravel-deploy demo2
```

Writes `Dockerfile`, `compose.yml`, `.dockerignore` tuned for Coolify's
docker-compose build path.

### 4. Create the GitHub repo + first push

```bash
new-repo demo2
# Private under batjaa/ by default; --public to flip.
```

Creates `batjaa/demo2`, sets `origin`, normalizes branch to `main`, first
commit, push.

### 5. Roll out the preview deployment on wormmon

```bash
new-wormmon-app demo2
```

Needs the `COOLIFY_*` env vars (see below). Creates:

- Coolify project `demo2`
- `production` environment under it
- An ed25519 deploy key (`github-batjaa-demo2`) registered on both Coolify
  (so it can pull the private repo) and GitHub (as a per-repo deploy key)
- A Coolify application bound to `demo2.preview.batjaa.site`
- Queues the first deployment

It prints the deployment UUID. Poll status:

```bash
coolify_api_token="$(op read "${COOLIFY_TOKEN_REF:-op://Private/Coolify/API Token}")"
curl -s -H "Authorization: Bearer $coolify_api_token" \
  "$COOLIFY_URL/api/v1/deployments/<uuid>" | jq -r .status
unset coolify_api_token
```

Builds typically take ~2-3 min (Laravel + Composer + Vite). When status
is `finished`, hit `https://demo2.preview.batjaa.site`.

### Required environment

Non-secret settings and the 1Password secret reference are sourced from
`~/.extra` (gitignored, `chmod 600`, loaded by `.bash_profile`):

```bash
export COOLIFY_URL="https://deploy.batjaa.site"
export COOLIFY_TOKEN_REF="op://Private/Coolify/API Token"
export COOLIFY_SERVER_UUID="..."     # the localhost server in Coolify
export COOLIFY_DESTINATION_UUID="..." # the localhost-default Docker destination
```

The plaintext Sanctum token lives only in the concealed **API Token** field on
the `Private/Coolify` 1Password item. `new-app` and `new-wormmon-app` resolve
the reference at runtime with `op read`; the 1Password desktop app must be
unlocked with CLI integration enabled. A plaintext `COOLIFY_TOKEN` remains a
supported override for CI or a short-lived shell, but must not be stored in
`~/.extra`, a dotenv file, or this repository.

If `~/.extra` is wiped:

- `COOLIFY_URL` — `https://deploy.batjaa.site`
- `COOLIFY_SERVER_UUID` / `COOLIFY_DESTINATION_UUID` — query the API:
  `GET /api/v1/servers` and `GET /api/v1/destinations` (or copy from the
  Coolify UI's URL bar on the Server / Destination pages)
- `COOLIFY_TOKEN_REF` — `op://Private/Coolify/API Token`. Verify it with
  `op read "$COOLIFY_TOKEN_REF" >/dev/null`; this checks access without
  printing the token.
- If the 1Password token is missing or rejected, it must be freshly minted
  because Sanctum stores only the hash. Coolify UI → top-right avatar →
  **Keys & Tokens** → New API Token, then immediately replace the **API
  Token** field on the `Private/Coolify` item. If the UI is locked out, see
  "Recovering a token" below.

---

## Gaps and gotchas observed

Caught while wiring up `demo` and `demo1`.

1. **`COOLIFY_*` settings not persisted on first setup.** Non-secret values
   and `COOLIFY_TOKEN_REF` now live in `~/.extra` (sourced by
   `.bash_profile`, `chmod 600`, gitignored). The plaintext token lives in
   `Private/Coolify` in 1Password and is resolved only at runtime. Do NOT put
   the token in `.extra` or `.exports` — the latter is committed to
   `batjaa/settings`.

2. **`new-laravel` wrote `\\` instead of `\` in `bootstrap/app.php`.**
   The `trustProxies` injection used `Illuminate\\\\Http\\\\Request` in
   a PHP double-quoted source string, which becomes literal `\\Http\\`
   on disk — invalid PHP outside a string. **Fixed in
   `app-bootstrap@f293d0d`** (source reduced to `\\Http\\` → `\Http\`
   on disk).

3. **`new-wormmon-app` double-added the GitHub deploy key.** After the
   first block called `gh repo deploy-key add`, `$github_deploy_key_id`
   wasn't refreshed; the fallback block then re-added the same key and
   hit GitHub's 422 "key is already in use". **Fixed in
   `app-bootstrap@f293d0d`** (refresh the id right after the gh API
   call).

4. **Coolify upgrades reset `/data/coolify` perms and break scheduled DB
   backups.** The install/upgrade script chowns the tree to uid 9999
   (in-container www-data) with mode 700, but the scheduled backup writes
   its dump over SSH as `batjaa` and needs traverse (`o+x`) into
   `backups/` — every nightly backup then fails with `Permission denied`
   (bit us after the 4.1.2 upgrade on 2026-07-24). The coolify role now
   converges `/data/coolify{,/backups,/backups/coolify}` to `9999:root
   0711`; **re-run `ansible-playbook main.yml -l wormmon --tags coolify`
   after any Coolify upgrade.**

5. **Failure mid-pipeline leaves partial state in three places.** A
   failed `new-wormmon-app` can leave behind a Coolify project, a
   Coolify deploy key, *and* a GitHub deploy key. No rollback. Manual
   cleanup before retrying:

   ```bash
   APP=demoN
   coolify_api_token="$(op read "${COOLIFY_TOKEN_REF:-op://Private/Coolify/API Token}")"

   # Coolify project
   PROJ_UUID=$(curl -s -H "Authorization: Bearer $coolify_api_token" "$COOLIFY_URL/api/v1/projects" \
     | jq -r --arg n "$APP" '.[] | select(.name == $n) | .uuid')
   [[ -n "$PROJ_UUID" ]] && curl -X DELETE -H "Authorization: Bearer $coolify_api_token" \
     "$COOLIFY_URL/api/v1/projects/$PROJ_UUID"

   # Coolify deploy key
   KEY_UUID=$(curl -s -H "Authorization: Bearer $coolify_api_token" "$COOLIFY_URL/api/v1/security/keys" \
     | jq -r --arg n "github-batjaa-$APP" '.[] | select(.name == $n) | .uuid')
   [[ -n "$KEY_UUID" ]] && curl -X DELETE -H "Authorization: Bearer $coolify_api_token" \
     "$COOLIFY_URL/api/v1/security/keys/$KEY_UUID"

   # GitHub deploy key
   GH_KEY=$(gh api "repos/batjaa/$APP/keys" --jq --arg t "coolify-$APP" '.[] | select(.title == $t) | .id')
   [[ -n "$GH_KEY" ]] && gh api -X DELETE "repos/batjaa/$APP/keys/$GH_KEY"

   unset coolify_api_token
   ```

   Then re-run `new-wormmon-app $APP`. The Laravel project + GitHub repo
   themselves can stay — `new-wormmon-app` is idempotent against them.

### Recovering a token

Sanctum stores only the hash, so a lost token can't be read back. If the
Coolify UI is reachable, mint a fresh one there and immediately replace the
concealed **API Token** field on the `Private/Coolify` 1Password item. If the
UI is unavailable, create the token via artisan and pipe it directly into
1Password without printing it or writing it to disk. Coolify's
`User::createToken()` override reads `session('currentTeam')`, which is `null`
under tinker, so the recovery command builds the row directly:

```bash
coolify_recovered_token="$(ssh batjaa@wormmon.home.local 'sudo docker exec coolify php artisan tinker --execute="
  \$plain=bin2hex(random_bytes(32));
  \$row=new Laravel\\Sanctum\\PersonalAccessToken();
  \$row->tokenable_type=\"App\\Models\\User\"; \$row->tokenable_id=0; \$row->team_id=0;
  \$row->name=\"recovery\"; \$row->token=hash(\"sha256\", \$plain); \$row->abilities=[\"*\"];
  \$row->save();
  echo \$row->id.\"|\".\$plain.\"\\n\";
"' | tail -n 1)"

[[ "$coolify_recovered_token" == *"|"* ]] || {
  echo "Token recovery failed; 1Password was not changed" >&2
  unset coolify_recovered_token
  false
}

op item get Coolify --vault Private --format=json \
  | jq --arg token "$coolify_recovered_token" '
      if any(.fields[]; .label == "API Token") then
        .fields |= map(if .label == "API Token" then .type = "CONCEALED" | .value = $token else . end)
      else
        .fields += [{"id":"coolify_api_token","type":"CONCEALED","label":"API Token","value":$token}]
      end
    ' \
  | op item edit Coolify --vault Private >/dev/null
unset coolify_recovered_token
```

The Sanctum format is `<id>|<plaintext>` — that whole string is the
token value saved in 1Password. Revoke any superseded token from the UI once
you regain access.


## Additional production apps

These apps use `coolify-production-apps.subdomain.conf.j2` and share the
Cloudflare → andromon/SWAG → wormmon/Coolify ingress path:

| Domain | Repository branch | Services |
| --- | --- | --- |
| `fotopass.app` | `batjaa/fotopass` `main` | Web, MySQL, queue worker, Nightwatch |
| `mytendies.app` | `batjaa/tendies` `main` | Web, MySQL, queue worker, Nightwatch |
| `heyanda.mn` | `batjaa/anda` `main` | Web, MySQL, Neo4j, queue worker |
| `tech-nomads.io` | `batjaa/tech-nomads` `main` | Web, MySQL, Nightwatch |
| `tsas.mn` | `batjaa/tsas` `master` | Web, MySQL |

The apex DNS records point to `ddns.batjaa.site`. `www` redirects to the apex
for Fotopass, Tendies, Tech Nomads, and TSAS. Heyanda preserves its wildcard
subdomains: SWAG selects Coolify using the apex upstream Host and forwards the
original hostname to Laravel. Pi-hole resolves these hosts to the LAN ingress.
Existing mail, R2 media, and Tendies staging records remain separate.

Each project's production secrets and recovery source environment are stored
in its own 1Password Private-vault secure note, titled
`<domain> — Production (Coolify)`. Tendies also requires its original Passport
signing keys in persistent storage; both keys are included in its secure note.
Fotopass's scheduler stays disabled, matching its previous production setup.

The DigitalOcean apps and databases remain available for recovery in maintenance
mode, with their migrated Supervisor workers and Nightwatch daemons disabled.
`staging.mytendies.app` continues running on DigitalOcean. Before any rollback,
copy the current Coolify database and local storage back to the old deployment,
then restore the old DNS and Supervisor settings. The retained migration
snapshots alone do not contain writes made after the cutover.
