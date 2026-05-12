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

`*.batjaa.site` does not cover `*.preview.batjaa.site`, because that is a
second-level wildcard. SWAG therefore needs explicit additional certificate
coverage for:

- `preview.batjaa.site`
- `*.preview.batjaa.site`

For third-party app domains like `plotling.app`, add those domains to the SWAG
certificate configuration before exposing them publicly.
