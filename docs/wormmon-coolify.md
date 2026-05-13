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

## Certificate notes

`*.batjaa.site` does not cover `*.preview.batjaa.site` — wildcards only
match one label deep (RFC 6125). SWAG needs explicit additional coverage
for **`*.preview.batjaa.site` only**, not `preview.batjaa.site`, since
that single-label name is already covered by `*.batjaa.site`. Let's
Encrypt rejects requests that include both a single-label name and its
parent wildcard ("redundant with a wildcard domain in the same request").

The knob is `swag_extra_domains` in `host_vars/andromon/vars.yml`:

```yaml
swag_extra_domains: "*.preview.{{ host }}"
```

For third-party app domains like `plotling.app`, add those domains to
`swag_extra_domains` (comma-separated) before exposing them publicly.

### Forcing a cert re-issue after changing `swag_extra_domains`

SWAG only checks expiry on startup, not SAN drift. If you change
`swag_extra_domains` while the existing cert is still valid, it won't
be re-issued until overnight renewal. To force:

```bash
ssh -p 100 batjaa@192.168.50.20 'docker exec swag certbot certonly \
  --config-dir /config/etc/letsencrypt \
  --work-dir /config/var/lib/letsencrypt \
  --logs-dir /config/var/log/letsencrypt \
  --non-interactive --agree-tos --expand \
  --authenticator dns-cloudflare \
  --dns-cloudflare-credentials /config/dns-conf/cloudflare.ini \
  --cert-name batjaa.site \
  -d batjaa.site,*.batjaa.site,*.preview.batjaa.site \
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
curl -s -H "Authorization: Bearer $COOLIFY_TOKEN" \
  "$COOLIFY_URL/api/v1/deployments/<uuid>" | jq -r .status
```

Builds typically take ~2-3 min (Laravel + Composer + Vite). When status
is `finished`, hit `https://demo2.preview.batjaa.site`.

### Required environment

Sourced from `~/.extra` (gitignored, `chmod 600`, loaded by `.bash_profile`):

```bash
export COOLIFY_URL="https://deploy.batjaa.site"
export COOLIFY_TOKEN="..."           # Sanctum personal access token, ["*"] abilities
export COOLIFY_SERVER_UUID="..."     # the localhost server in Coolify
export COOLIFY_DESTINATION_UUID="..." # the localhost-default Docker destination
```

If `~/.extra` is wiped:

- `COOLIFY_URL` — `https://deploy.batjaa.site`
- `COOLIFY_SERVER_UUID` / `COOLIFY_DESTINATION_UUID` — query the API:
  `GET /api/v1/servers` and `GET /api/v1/destinations` (or copy from the
  Coolify UI's URL bar on the Server / Destination pages)
- `COOLIFY_TOKEN` — **must be freshly minted** (Sanctum stores only the
  hash). Coolify UI → top-right avatar → **Keys & Tokens** → New API
  Token. If the UI is locked out, see "Recovering a token" below.

---

## Gaps and gotchas observed

Caught while wiring up `demo` and `demo1`.

1. **`COOLIFY_*` env vars not persisted on first setup.** They now live
   in `~/.extra` (sourced by `.bash_profile`, `chmod 600`, gitignored).
   Do NOT put them in `.exports` — that file is committed to
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

4. **Failure mid-pipeline leaves partial state in three places.** A
   failed `new-wormmon-app` can leave behind a Coolify project, a
   Coolify deploy key, *and* a GitHub deploy key. No rollback. Manual
   cleanup before retrying:

   ```bash
   APP=demoN

   # Coolify project
   PROJ_UUID=$(curl -s -H "Authorization: Bearer $COOLIFY_TOKEN" "$COOLIFY_URL/api/v1/projects" \
     | jq -r --arg n "$APP" '.[] | select(.name == $n) | .uuid')
   [[ -n "$PROJ_UUID" ]] && curl -X DELETE -H "Authorization: Bearer $COOLIFY_TOKEN" \
     "$COOLIFY_URL/api/v1/projects/$PROJ_UUID"

   # Coolify deploy key
   KEY_UUID=$(curl -s -H "Authorization: Bearer $COOLIFY_TOKEN" "$COOLIFY_URL/api/v1/security/keys" \
     | jq -r --arg n "github-batjaa-$APP" '.[] | select(.name == $n) | .uuid')
   [[ -n "$KEY_UUID" ]] && curl -X DELETE -H "Authorization: Bearer $COOLIFY_TOKEN" \
     "$COOLIFY_URL/api/v1/security/keys/$KEY_UUID"

   # GitHub deploy key
   GH_KEY=$(gh api "repos/batjaa/$APP/keys" --jq --arg t "coolify-$APP" '.[] | select(.title == $t) | .id')
   [[ -n "$GH_KEY" ]] && gh api -X DELETE "repos/batjaa/$APP/keys/$GH_KEY"
   ```

   Then re-run `new-wormmon-app $APP`. The Laravel project + GitHub repo
   themselves can stay — `new-wormmon-app` is idempotent against them.

### Recovering a token

Sanctum stores only the hash, so a lost token can't be read back. If the
Coolify UI is reachable, mint a fresh one there. If it's not, do it via
artisan on the box. Note Coolify's `User::createToken()` override reads
`session('currentTeam')` which is `null` under tinker, so you have to
build the row directly:

```bash
ssh batjaa@wormmon.home.local 'sudo docker exec coolify php artisan tinker --execute="
  \$plain=bin2hex(random_bytes(32));
  \$row=new Laravel\\Sanctum\\PersonalAccessToken();
  \$row->tokenable_type=\"App\\Models\\User\"; \$row->tokenable_id=0; \$row->team_id=0;
  \$row->name=\"recovery\"; \$row->token=hash(\"sha256\", \$plain); \$row->abilities=[\"*\"];
  \$row->save();
  echo \$row->id.\"|\".\$plain.\"\\n\";
"'
```

The Sanctum format is `<id>|<plaintext>` — that whole string is the
`COOLIFY_TOKEN` value. Revoke from the UI once you regain access.
