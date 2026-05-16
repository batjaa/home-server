# Local AI Agents

How to think about the local AI stack in this repo, and how to grow it without
turning Open WebUI into a pile of one-off integrations.

## Mental model

Use three layers:

1. **Inference layer**: Ollama on `greymon`
   - Runs the actual models.
   - Should stay simple: model hosting, VRAM/CPU scheduling, model pulls.
   - No app-specific logic here.

2. **Orchestration/UI layer**: Open WebUI on `andromon`
   - Human-facing chat UI.
   - Handles users, conversations, model selection, and tool calling.
   - Talks to Ollama over HTTP and to local tool servers over Docker network.

3. **Tool/agent layer**: narrow service integrations on `andromon`
   - Each agent should do one job well.
   - Example: `movie-agent` answers questions about Radarr/Seerr and can later
     create requests, inspect queue state, or explain why an import failed.
   - These are regular HTTP APIs with auth, described by OpenAPI, and
     registered into Open WebUI as tools.

That separation matters:

- Ollama is replaceable. If you swap to vLLM or OpenAI later, your service
  agents do not need to change.
- Open WebUI is replaceable. If you move to another chat frontend, the agents
  stay usable because they are plain APIs.
- Agents are composable. You can add `movie-agent`, `calendar-agent`,
  `paperless-agent`, `homeassistant-agent`, or `storage-agent` independently.

## What belongs in an agent

Good agent boundaries:

- One domain owner: movies, books, documents, home automation, storage.
- Small input/output surface.
- Explicit auth.
- Deterministic tool behavior.

Bad agent boundaries:

- A single "god agent" that knows Radarr, Sonarr, SABnzbd, Plex, and Nextcloud.
- UI logic in the agent.
- LLM prompting embedded into every tool call.

Prefer this pattern:

- Open WebUI chooses the model.
- Open WebUI decides whether to call a tool.
- The tool server executes domain logic and returns structured JSON.

## Current repo shape

Already present:

- `roles/containers/services/open-webui`
- `roles/containers/agents/movie-agent`
- `agents/cmd/movie-agent`
- SWAG internal vhosts for:
  - `chat.{{ host }}`
  - `ollama.{{ host }}`

Current traffic flow:

```text
browser
  -> https://chat.batjaa.site
  -> SWAG on andromon
  -> Open WebUI container on andromon
  -> Ollama on greymon

Open WebUI
  -> agents-net
  -> movie-agent container on andromon
  -> Radarr / Seerr / other internal APIs
```

## Recommended rollout order

1. Get Ollama stable on `greymon`.
2. Get Open WebUI stable on `andromon`.
3. Add one narrow tool server end to end.
4. Confirm the model can discover and use that tool.
5. Only then add more agents.

That avoids debugging four moving parts at once.

## Open WebUI setup in this repo

The Open WebUI role already does the important base wiring:

- publishes `127.0.0.1:3014 -> 8080`
- points `OLLAMA_BASE_URL` at `http://192.168.50.30:11434`
- joins the shared `agents-net` Docker network
- seeds tool servers through `TOOL_SERVER_CONNECTIONS`

### Host vars on `andromon`

In [`host_vars/andromon/vars.yml`](/Users/batjaa/git/home-server/host_vars/andromon/vars.yml),
the required toggles are already enabled:

```yaml
enable_open_webui: true
enable_movie_agent: true
```

The default Open WebUI role variables are in
[`roles/containers/services/open-webui/defaults/main.yml`](/Users/batjaa/git/home-server/roles/containers/services/open-webui/defaults/main.yml).

Important ones:

```yaml
open_webui_port: 3014
open_webui_ollama_url: "http://192.168.50.30:11434"
open_webui_tool_servers:
  - url: "http://movie-agent:8080"
    path: "openapi.json"
    type: "openapi"
```

### Secret vars

Put agent bearer tokens in `host_vars/andromon/secret.yml`.

Current minimum:

```yaml
movie_agent_token: "<long-random-token>"
```

Generate one:

```bash
openssl rand -hex 32
```

### Deploy

Run:

```bash
ansible-playbook main.yml --limit andromon --tags open_webui,movie-agent,swag
```

If Docker itself was not already converged on the host, include `docker` too:

```bash
ansible-playbook main.yml --limit andromon --tags docker,open_webui,movie-agent,swag
```

### First-run admin setup

Open `https://chat.batjaa.site`.

- The first account created becomes the Open WebUI admin.
- Log in and confirm the Ollama connection is visible.
- Pull or expose at least one coding-capable model in Ollama first.

Practical starter model choices:

- `qwen2.5-coder`
- `deepseek-coder-v2` if your hardware can tolerate it
- a general model as fallback for non-coding tasks

### Verify tool registration

The current role seeds tools through the `TOOL_SERVER_CONNECTIONS` env var.
That is good for first boot, but Open WebUI treats this as persisted config.
If the DB already exists, changing the env var alone may not update tool
registrations.

So use this rule:

- First deployment: env seeding is enough.
- Later tool changes: either wipe the Open WebUI data volume or add an API
  bootstrap task that updates tools through the admin API.

## How to add more service agents

Use the movie agent as the pattern.

For each new agent:

1. Create a small containerized HTTP service.
2. Add bearer-token auth.
3. Expose `/openapi.json`.
4. Put it on `agents-net`.
5. Register it in `open_webui_tool_servers`.
6. Keep the service focused on one domain.

Examples that fit your stack well:

- `movie-agent`
  - search Radarr library
  - inspect missing movies
  - check quality profiles
  - create Seerr/Radarr requests

- `tv-agent`
  - Sonarr queue status
  - missing episodes
  - series search / request

- `download-agent`
  - SABnzbd queue
  - failed download explanations
  - category backlog

- `media-status-agent`
  - Plex/Jellyfin current streams
  - transcode count
  - why playback is transcoding

- `paperless-agent`
  - document search
  - tag lookup
  - upload workflow helper

## Coding agents speaking to Ollama or Open WebUI

There are two different integration targets:

### 1. Coding agents talk to Ollama directly

Use this when you want a local coding model endpoint.

Pros:

- simplest path
- no Open WebUI dependency
- best for editor plugins or CLI agents

Typical shape:

```text
coding client -> Ollama HTTP API -> local model
```

### 2. Coding agents talk to Open WebUI

Use this when you want:

- shared auth and user management
- tool calling through your service agents
- conversation history
- one place to manage model access

Typical shape:

```text
coding client -> Open WebUI API/UI -> Ollama + tools
```

For pure code generation, direct Ollama is usually cleaner. For "coding plus
operate the homelab", Open WebUI becomes more useful because it can broker tool
access to your internal agents.

## Practical recommendation for your setup

Use both:

- **Ollama** as the stable model backend.
- **Open WebUI** as the human chat frontend and tool orchestrator.
- **Small tool agents** for each service domain.

That gives you:

- local chat for everyday use
- homelab-aware assistants for movies and services
- a future path for coding agents to either call Ollama directly or go through
  Open WebUI when they need tools

## Current limitations

- `movie-agent` is scaffolded but not yet connected to Radarr or Seerr.
- Open WebUI tool registration is seed-on-first-boot right now.
- `greymon` is Windows and not Ansible-managed, so Ollama lifecycle is still a
  manual dependency.
