# Project preview namespaces

Register a project once to serve `PROJECT.preview.batjaa.site` and
`SERVICE.PROJECT.preview.batjaa.site` through its approved Coolify server.
Application deployments only select their hostname; they do not edit Nginx or
request certificates. The GoDaddy/production-domain migration is independent.

## Source of truth

- `vars/preview-projects.json`: namespace → target; bootstrap writes this idempotently.
- `vars/preview-targets.json`: approved Coolify server UUID → edge IP/port.
- `preview-projects.yml`: DNS, isolated project TLS and proxy convergence.
- `roles/network/preview-projects/`: shared route, rollback and renewal logic.

The existing flat preview wildcard remains supported. Each registered project
gets explicit DNS-only root/wildcard CNAMEs pointing to DDNS, plus a certificate
covering its root and `*.PROJECT.preview.batjaa.site`. Wildcard TLS covers one
service label; `deep.api.PROJECT.preview.batjaa.site` is rejected by bootstrap.

Jolly uses the `tech-nomads` target through wormmon's existing restricted `8443`
relay. Normal wormmon apps use port `443`. Targets are selected by server UUID;
an app manifest cannot supply an arbitrary upstream address. An existing namespace
cannot silently move to another target.

## Register and apply

```sh
bin/register-preview-project version
bin/register-preview-project register jolly --server-uuid b3mwnfgzbdyjtb7uuzrz0qlw --dry-run
bin/register-preview-project register jolly --server-uuid b3mwnfgzbdyjtb7uuzrz0qlw --apply
```

`--dry-run` does not write files, read credentials or contact remote services.
`check` requires an existing matching registration. A normal registration writes
JSON atomically under a directory lock. `--apply` invokes Ansible for that project;
failed application leaves the desired registration visible so a retry converges.
The script never commits or pushes. Commit the generated registry entry in this
repo to preserve it for disaster recovery. New projects need an entry, not a new
role or certificate/proxy template; new services in an existing namespace do not
change this registry.

The companion `app-bootstrap` repo calls registration automatically before any
Coolify creation. Its manifest supports:

```yaml
preview_domain: app.jolly.preview.batjaa.site
preview:
  project: jolly
  service: app
```

Old flat manifests infer their project from the hostname. Coolify still needs
that exact service hostname in its routing configuration. No unregistered app is
made available merely because wildcard DNS resolves it.

## Operations and recovery

Apply all registry entries with `ansible-playbook preview-projects.yml`, or select
one with `--extra-vars '{"preview_project_filter":"jolly"}'`. The playbook is also
included in `main.yml` under `--tags preview-projects`. Existing Pi-hole suffix
routing for `preview.batjaa.site` already covers nested names.

Certbot uses the existing SWAG Cloudflare DNS credentials and isolated state at
`/opt/docker/data/swag/nginx/preview-letsencrypt`. A twice-daily cron renews these
certificates and validates/reloads Nginx after successful renewal. The large shared
SWAG certificate is unchanged. Issuance precedes route installation; failed Nginx
validation restores the previous route before any reload. A registered project
can list specific `legacy_routes` filenames for removal after replacement validates.
This migrated Jolly's old landing preview route; its old flat app URL remains an
alias for existing links.

Exercise renewal without replacing the live certificate:

```sh
ansible-playbook preview-projects.yml --extra-vars '{"preview_project_filter":"jolly","preview_test_renewal":true}'
```

Restore the Git registry and persistent SWAG directory, then reapply. Removing an
entry does not delete DNS, certificates or old routes automatically; retire those
explicitly after confirming no deployed app uses the namespace. Register known
projects only; there is no public/on-demand certificate issuance endpoint.

## Verification

- `python3 -m unittest discover -s tests -p 'test_preview_registry.py'`
- First application, then repeat and require `changed=0`.
- Verify TLS, `/health`, host routing, and unsigned WebSocket rejection.
- Jolly: `https://app.jolly.preview.batjaa.site/health`; legacy app and landing
  URLs must continue working.

References: [Certbot DNS validation and renewal](https://eff-certbot.readthedocs.io/en/stable/using.html),
[Cloudflare DNS plugin](https://certbot-dns-cloudflare.readthedocs.io/en/stable/).
