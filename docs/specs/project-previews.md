# Registered project previews

Give each project a stable `PROJECT.preview.batjaa.site` namespace and a DNS-01
certificate covering its root and one service label. Keep production DNS separate.

1. A versioned JSON registry maps project names to approved Coolify server targets.
   Registration validates labels/server identity, is idempotent and refuses silently
   moving an existing project. Dry runs have no writes, secret reads or network calls.
2. Ansible creates explicit root/wildcard DNS records, an isolated project certificate,
   a root/wildcard proxy route and shared automated renewal. Existing routes remain
   available if issuance fails; validate Nginx before reload. Reapply has zero changes.
3. Bootstrap records preview project/service metadata, registers the namespace before
   any Coolify mutation, and keeps existing flat manifests compatible. Registration
   changes remain visible in Git; bootstrap never commits unrelated files or pushes.
4. Register Jolly on the Tech Nomads target and add app.jolly.preview.batjaa.site to
   its Coolify routes. Preserve the old app URL and landing route. Verify HTTPS,
   health, login and WebSockets, and preserve provider setup states.
5. Tests cover invalid/conflicting names, same-project multi-service reuse, unknown
   server, dry-run behavior, manifest compatibility and bootstrap ordering. Document
   first-project registration, recovery, renewal, limits, and migration.
