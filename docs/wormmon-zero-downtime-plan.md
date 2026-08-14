# Wormmon / Coolify — zero-downtime deployment plan

Status: **planned** (nothing implemented yet). Companion to
[wormmon-coolify.md](wormmon-coolify.md); the script changes land in the
separate [`batjaa/app-bootstrap`](https://github.com/batjaa/app-bootstrap)
repo, not here.

## Problem

Every app `new-wormmon-app` creates uses Coolify's **Docker Compose build
pack** (`build_pack: "dockercompose"`), with `app` + `worker` + `scheduler` +
`db` in one stack. Coolify's native zero-downtime mechanism ("rolling
updates": start new container → wait for health check → swap Traefik routing
→ stop old) is **explicitly unsupported for compose-based apps** — compose
uses static container names, so old and new containers can't overlap. Result:
every push to `main` recreates the `app` container in place, and the site
serves 502s for the recreate + entrypoint-migration + `artisan serve` boot
window (~5–15 s).

Compose rolling updates are discussed for Coolify v5
([discussion #3767](https://github.com/coollabsio/coolify/discussions/3767));
on 4.x they don't exist. Workarounds like `docker-rollout` are off the table —
prefer the native path.

## Coolify's requirements for rolling updates

All four must hold
([docs](https://coolify.io/docs/knowledge-base/rolling-updates)):

1. **Not the Docker Compose build pack** — Dockerfile / Nixpacks / Static only
2. **A configured, passing health check** — Coolify refuses to swap traffic
   without one (the container needs `curl` or `wget`; our generated Dockerfile
   already installs curl)
3. **Default container naming** — never enable "Consistent Container Names"
4. **No host port mappings** — Traefik-routed only (already true)

## Target architecture (per app)

Split the single compose stack into resources inside the same Coolify project:

| Resource | Coolify type | Notes |
|---|---|---|
| Web | Application, **Dockerfile build pack** | Same generated Dockerfile (CMD `app`). Domain attached. Health check on Laravel's `/up`. "Connect to Predefined Network" on so it reaches the db. **Gets rolling updates.** |
| Database | **Managed database resource** (see options below) | Decoupled from app deploys; internal hostname on the destination network; native scheduled S3 backups → existing R2 setup. |
| Worker + scheduler | Application, compose build pack (no domain) | A few seconds of queue/scheduler restart is invisible to users, so compose downtime is fine here. Separate Dockerfile apps would double builds for no user-facing benefit. |

### Laravel-side requirements

- **Backward-compatible migrations** (expand/contract). During the swap the
  new container has migrated while the old one still serves. Discipline, not
  tooling.
- **Health endpoint**: Laravel's built-in `/up` route.
- **Entrypoint**: drop the `|| true` on `php artisan migrate --force`. A
  failed migration must fail the health check so the old container keeps
  serving; today the failure is swallowed and a broken container goes live.
- Sessions / cache / queue are already database-backed in the template —
  nothing sticky lives in the container. Keep it that way.
- Optional, later: replace `php artisan serve` (single-threaded dev server)
  with FrankenPHP or php-fpm + nginx. Not required for zero downtime.

## Database options

What Coolify on wormmon can host, and how the bootstrap should treat each.

### Native one-click database resources

PostgreSQL, MySQL, MariaDB, MongoDB, Redis, KeyDB, Dragonfly, ClickHouse.
These get internal hostnames, lifecycle independent of app deploys, and
Coolify's **scheduled S3 backups** (→ R2, same as the coolify-db backup).

- **Postgres** — stays the default for real apps.
- **MongoDB** — the "DynamoDB-like" document option. Native resource, official
  `mongodb/laravel` package. A true DynamoDB API would mean `dynamodb-local`
  (test-only, not production) or ScyllaDB's Alternator (heavy) — use Mongo
  instead.
- **Redis / KeyDB / Dragonfly** — cache/queue upgrades when the database
  driver stops being enough.

### SQLite — the "simple app" tier

Not a Coolify resource at all: it's a file inside the app. For low-traffic
apps this removes the Postgres container entirely (~100–200 MB RAM each).

- `DB_CONNECTION=sqlite`, database file on a **host bind mount**
  (`/opt/docker/data/apps/<name>/database.sqlite`) so it survives deploys and
  is shared by web + worker containers.
- Enable WAL + `busy_timeout` (Laravel 11+ exposes these in
  `config/database.php`). WAL makes the brief old/new container overlap during
  a rolling update safe (concurrent readers + one writer on the same host
  file).
- Simple apps can drop the worker/scheduler stack entirely
  (`QUEUE_CONNECTION=sync`); keep it only when the app actually queues.
- Backup story: the bind mount dir must be covered by a backup (wormmon has no
  host backup today — either add the dir to a scheduled task or accept the
  risk per-app). Coolify's DB backup feature does not apply.

### One-click service templates (not "database resources")

Verified present in the current template catalog:

- **Elasticsearch** (and `elasticsearch-with-kibana`) — exists, but ES wants
  1.5–2 GB+ RAM for itself. For Laravel side projects, **Meilisearch** or
  **Typesense** (both one-click, both first-party Laravel Scout drivers) are
  the better default.
- **Vector DBs**: Qdrant, Weaviate, Chroma — all one-click, useful for AI
  projects.
- **RabbitMQ** — if a real broker ever beats database/Redis queues.

Caveat: service templates do **not** get Coolify's scheduled DB backup
feature. Anything stateful deployed this way needs its own backup answer
before it holds real data.

### Not in the catalog

- **Neo4j** — no template. Deploy as a one-off Docker Compose *resource*
  (paste a compose file in Coolify). It isn't user-facing, so compose's
  deploy downtime doesn't matter. Same backup caveat as services.

### Manifest implication

`.batjaa/app.yml`'s `database.driver` becomes a real switch:

- `sqlite` → no DB resource; bind-mounted data dir; optional `queue: false`
  to skip the worker stack
- `postgres` (default) / `mysql` / `mariadb` / `mongodb` → create the matching
  Coolify database resource, wire env accordingly
- optional `services:` list later (meilisearch, redis, …) if a project needs
  sidecars

## Script changes (app-bootstrap)

### `new-laravel-deploy`

- Compose file: generate only `worker` + `scheduler` (drop `app` and `db`
  services). Skip entirely for sqlite apps without a queue.
- Entrypoint: remove `|| true` from migrate; add a short DB wait/retry loop
  (the web app loses `depends_on: db`).
- Bake in the two known first-deploy gotchas (see "Gaps and gotchas" in
  [wormmon-coolify.md](wormmon-coolify.md)): `mkdir -p storage/framework/*`
  inside the build `RUN` layer.

### `new-wormmon-app`

- Create the database via API (`POST /databases/postgresql` etc.) instead of
  a compose service; reuse the generated credentials; read back the internal
  host for `DB_HOST`.
- Create the web app with `build_pack: "dockerfile"` +
  `dockerfile_location: "/Dockerfile"`, then PATCH
  `health_check_enabled: true`, `health_check_path: "/up"`, and enable
  connect-to-predefined-network.
- Create the worker/scheduler compose app as a second application (no
  domain).
- Wire auto-deploy (tinker settings flip + GitHub push webhook) for **both**
  applications.
- Side effect: the web app's envs become plain runtime vars — no compose
  interpolation — so the "runtime-only env breaks compose build" gotcha
  disappears for it (still applies to the worker stack).

## Existing apps

`tipped`, `bet-mn`, `summit-todo`, `demo` were born as compose apps; the
script changes only fix apps created from now on. Each needs a one-time
manual conversion in Coolify (new Dockerfile app + managed DB + data
migration + domain move). Use `demo` as the guinea pig before touching
anything with users.

## Rollout order

1. Patch `new-laravel-deploy` + `new-wormmon-app` in app-bootstrap
2. Bootstrap a throwaway app end-to-end; verify a push deploys with zero
   dropped requests (`while true; do curl -s -o /dev/null -w '%{http_code}\n'
   https://<app>.preview.batjaa.site/up; sleep 0.5; done` across a deploy)
3. Convert `demo`, then the rest
4. Update the gotchas list in [wormmon-coolify.md](wormmon-coolify.md) and
   app-bootstrap's README

## Sources

- [Coolify: rolling updates](https://coolify.io/docs/knowledge-base/rolling-updates)
- [Coolify: health checks](https://coolify.io/docs/knowledge-base/health-checks)
- [Coolify: databases](https://coolify.io/docs/databases)
- [Coolify service-template catalog](https://github.com/coollabsio/coolify/blob/main/templates/service-templates.json)
- [Zero-downtime discussion #3767](https://github.com/coollabsio/coolify/discussions/3767)
- [Atomic Coolify + Laravel deployment (Matt Stein)](https://mattstein.com/thoughts/zero-downtime-laravel-coolify/)
