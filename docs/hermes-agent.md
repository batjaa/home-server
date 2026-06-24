# Hermes Agent — autonomous coding agent

Hermes ([nousresearch/hermes-agent](https://github.com/NousResearch/hermes-agent))
is an autonomous agent that takes work, does it, and opens pull requests. It is a
**different layer** from the chat-callable tool agents in [`agents.md`](agents.md):

- The `movie-agent` family are narrow, synchronous HTTP tools that Open WebUI
  calls during a chat turn.
- **Hermes is asynchronous and autonomous.** You hand it a task, it grinds on it
  in the background (minutes to hours), and reports back with a PR + a result
  note. It has its own persistent memory and reaches you over Telegram.

Hermes **reuses the existing inference layer** (Ollama on `greymon`) rather than
adding a new one.

## Division of labour

```
  laptop "Claude box"      greymon (.30, RTX 4070 Ti SUPER 16GB)   andromon (.20)
  ───────────────────      ─────────────────────────────────────   ──────────────
  Claude Code (loop):      Ollama  →  qwen2.5-coder:14b             Hermes container
   • writes task specs        OpenAI-compatible /v1  ◀──────────────  • reads vault/docs
   • reviews + merges PRs                                              • runs task in sandbox
  Obsidian vault (git) ──────────────────────────────────────────▶   • opens PR on code repo
                                                                       • Telegram in/out
  tentomon (.10): untouched infra      wormmon (.40): Coolify (app deploys, separate)

                         ┌──────────── GitHub (the bus) ────────────┐
                         │  vault repo  = task ledger + /docs truth   │
                         │  code repos  = work output (PRs only)      │
                         └────────────────────────────────────────────┘
```

- **Claude (laptop / spare laptop)** = the planner + reviewer. Interactive,
  human-in-the-loop. Writes task specs, reviews Hermes' PRs, makes the final
  merge call.
- **Hermes (andromon)** = the autonomous executor. One instance. Per task it
  clones the *target* repo into an ephemeral sandbox, works, opens a PR.
- **greymon** = the brawn. Ollama serves the coding model Hermes thinks with.
- **GitHub** = the coordination bus. No bespoke API; markdown-over-git.

### Why one Hermes instance, not one per repo

Hermes has subagent delegation + isolated sandboxes as first-class features, so a
single instance handles many repos by cloning each target into its own ephemeral
workspace. Repo isolation happens at the **workspace** level, not the container
level. Split into a second instance only for a concrete reason (hard credential
isolation between sensitive repos, a per-project model backend, fault isolation).
Don't pre-shard — N instances means N memory stores, N cred sets, N Telegram bots.

## The vault: `/docs` is truth, `/tasks` is the work queue

The Obsidian vault is a git repo (Obsidian Git plugin). It is the **system of
record**; Hermes' own `~/.hermes/memories/` is scratch/working state.

```
vault/
├── docs/                  ← durable truth. Claude OWNS and curates these.
│   ├── architecture.md        the big picture (mirror of this file)
│   ├── repos/<repo>.md        per-repo context Hermes reads before working
│   └── conventions.md
└── tasks/
    ├── inbox/             ← Claude + Telegram drop specs here
    ├── in-progress/       ← Hermes moves a spec here when it claims it = live board
    └── done/              ← + result note + PR link when finished
```

Discipline: a task spec is a **pointer** ("do X, see `docs/repos/foo.md`"), not a
context dump. Heavy, reusable knowledge lives in `/docs` so it's shared across
tasks. The `inbox → in-progress → done` directory move *is* the claim/dedup
mechanism — same idempotency discipline as the Ansible house rules.

## Task intake — Telegram is the doorbell, the vault is the ledger

Two intake doors, one ledger:

1. **Telegram (human, real-time).** Hermes' Telegram bot long-polls *outbound* to
   Telegram, so it needs **zero inbound exposure** — no webhook, no port-forward.
   You fire a one-liner from your phone; Hermes materialises it into
   `tasks/inbox/<date>-<slug>.md`, commits, then works it.
2. **Git (Claude).** Claude writes specs straight into `tasks/inbox/` and pushes;
   Hermes picks them up on its next vault sync.

Hermes reports back over Telegram on state changes ("PR #42 ready", "blocked,
need X").

## The PR loop — no babysitting

```
Hermes opens PR (feature branch only — cannot push to main)
   └─▶ Claude (long-running session on the spare laptop) reviews on a loop:
         gh pr list --author <bot> → read diff + vault/docs context → review
         ├─ changes requested → Hermes addresses, pushes to branch, re-requests ─┐
         │                                                                         │
         │◀──────────────── loops until clean, max N rounds ───────────────────────┘
         └─ approved + CI green → gh pr merge        ← the final call Claude reserves
   (Telegram pings you only on: merged ✓ / stuck after N rounds / needs a human call)
```

The reviewer is a **persistent Claude Code session** (via `/loop` or `/schedule`)
on the spare "Claude box", not the GitHub Action — it keeps context continuity and
can pull review context from `vault/docs`. A **max-round cap** (default 3) stops a
confused agent pair from ping-ponging forever; past the cap it escalates to you.

## GitHub credentials — the blast-radius fence

Hermes gets a **dedicated bot account / GitHub App**, never your personal PAT.
Fine-grained token, scoped to **only the target repos**:

| Permission | Why |
|---|---|
| Contents: **write** | push follow-up commits to **feature branches** (NOT to merge main) |
| Pull requests: **read & write** | open PRs, read review comments, re-request review |

`main` is **branch-protected** (require PR + passing CI + Claude approval). That
protection — not the token scope — is what makes `contents:write` safe: Hermes can
push to branches but physically cannot merge to `main`.

Token reaches Hermes as the `GITHUB_TOKEN` env var (from vault). Hermes does not
persist it to config files.

## Security: Hermes and the Docker socket

Hermes' sandboxing can use Docker as an execution backend (`HERMES_DOCKER_EXEC_*`).
**This role deliberately does NOT mount the host Docker socket** — doing so would
give an autonomous agent root-equivalent control of andromon, defeating the
"isolated environment" requirement. If/when Hermes needs container-based sandboxes,
the right answers are, in order of preference: its built-in non-Docker sandbox,
rootless Docker, a DinD sidecar, or a dedicated throwaway host — never the host
socket. Revisit this as a conscious decision, not a default.

## Model backend — greymon Ollama

Hermes points at greymon's existing Ollama over its OpenAI-compatible API:

- `OPENAI_BASE_URL=http://192.168.50.30:11434/v1`
- `OPENAI_API_KEY=ollama` (Ollama ignores it, but the client wants it non-empty)
- model in `config.yaml`: `qwen3-coder-64k` (see model requirements below — **not**
  qwen2.5-coder, which can't do native tool calls)

### Two hard model requirements (learned the hard way)

**1. Native, Ollama-parseable tool calls.** Hermes is entirely tool-driven. The model
must emit tool calls that Ollama parses into the structured `tool_calls` field — not
as bare JSON in the message content. **qwen2.5-coder (7b and 14b) FAILS this** — it
dumps `{"name":...,"arguments":...}` into content, which Hermes relays verbatim to
you (the "raw JSON in Telegram" symptom). Temperature/endpoint don't fix it; the
whole qwen2.5-coder family is unusable here. Verified working: **`qwen3-coder`** and
`gemma4:e4b`. qwen3.5 has known Ollama tool bugs — avoid.

**2. ≥64,000 token context.** Hermes hard-refuses models advertising/loading under
64k. `num_ctx` **cannot** be passed over Ollama's OpenAI `/v1` endpoint, so the fix
is a baked-in Modelfile variant.

### greymon model setup (Windows / manual)

```bash
ollama pull qwen3-coder:30b
# bake a 64k variant (num_ctx can't be set via /v1):
curl http://192.168.50.30:11434/api/create -d \
  '{"model":"qwen3-coder-64k","from":"qwen3-coder:30b","parameters":{"num_ctx":65536}}'
```

Hermes' `model.default` = `qwen3-coder-64k`. At 64k the 30B-A3B MoE needs ~33 GB
total; only ~14.5 GB fits the 4070 Ti SUPER's 16 GB and **~19 GB of KV cache spills
to system RAM** (greymon's 64 GB absorbs it). Because only ~3B params are active per
token, it stays **responsive for normal use** and only slows as a session fills
toward 64k. To reduce the spill, optionally set on greymon's Ollama and restart:

```
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KV_CACHE_TYPE=q8_0     # ~halves KV cache; q4_0 halves again
```

Fallbacks if 30B is ever too heavy: `qwen3:14b` (dense, fits 64k cleanly, most
stable local tool-caller) or `gemma4:e4b` (8B, 128k native, weaker coder).

### greymon sizing (RTX 4070 Ti SUPER, 16 GB; Ryzen 9800X3D; 64 GB DDR5)

| Model | Fits | Use for |
|---|---|---|
| **14B coder** (qwen2.5-coder:14b, Q4–Q5) | ✅ fully on GPU, ~32k ctx, fast | **daily workhorse** |
| **7–8B coder** | ✅ with KV headroom for parallel slots | the swarm option |
| **32B coder** (Q4 ≈ 19 GB) | ⚠️ partial CPU offload | one-shot heavy task, slow in a loop |

One 4070 Ti SUPER serves ~one strong agent or a *modest* swarm of light ones — a
big fan-out still serialises on a single GPU. If you want real parallelism, either
run a 7B with `OLLAMA_NUM_PARALLEL`, or route overflow subagents to a hosted model
(Nous Portal / Anthropic) via `config.yaml` `providers:`. greymon is **Windows +
not Ansible-managed**, so its Ollama lifecycle stays a manual dependency (same
caveat as `agents.md`); if greymon is down, Hermes is brain-dead — consider a
hosted fallback model for resilience.

## Deploy

```bash
# one-time: interactive setup writes ~/.hermes/config.yaml + first auth
ssh -p 100 batjaa@192.168.50.20 \
  "docker run -it --rm -v /opt/docker/data/hermes:/opt/data nousresearch/hermes-agent setup"

# converge the role
ansible-playbook main.yml -l andromon --tags="hermes,swag"
```

Required vault keys in `host_vars/andromon/secret.yml`:

```yaml
hermes_github_token: "<fine-grained PAT for the bot account>"
hermes_telegram_bot_token: "<from @BotFather>"
hermes_dashboard_password: "<basic-auth pw for agent.batjaa.site>"
hermes_api_server_key: "<openssl rand -hex 32>"
```

## Visibility

| Layer | Answers | Where |
|---|---|---|
| Hermes dashboard | who's working now, subagents | `agent.batjaa.site` (basic-auth, internal-only) |
| The vault | what work is in flight | `ls tasks/in-progress/` — renders as Kanban in Obsidian |
| Grafana | swarm size over time | optional textfile collector counting `in-progress/` |
| Telegram | tell me on change | push notifications + `/status` |

## Task handoff & multi-repo

Work reaches Hermes via its built-in **kanban** (durable SQLite board; the gateway
auto-dispatches `ready` cards every ~60s to workers that run in isolated git
worktrees). **One board per repo.**

**Onboard a new repo (one command):**
```bash
scripts/hermes-onboard-repo.sh <owner>/<repo>
```
It invites the bot + auto-accepts (using the bot's own token), branch-protects
`main`, clones the repo into `/opt/data/workspaces/<repo>` in the container
(chowned to the worker uid **1001**), and creates the board.

**Hand off tasks** — from a clone of the repo, run the global `/handoff` slash
command (`~/.claude/commands/handoff.md`) with a spec. It follows *that repo's*
`docs/agents/` conventions, decomposes the spec into PR-sized handoffs, persists
each as a vault note under `docs/agents/handoffs/`, and creates a thin card pointing
to it. Cards carry `--skill obsidian` (worker reads the vault + resolves wikilinks)
and `--skill github-pr-workflow` (worker opens its own PR), on a `feat/<slug>` branch.

**The worker opens its own PR** via Hermes' native `github-pr-workflow` skill,
force-loaded on the card with `--skill github-pr-workflow`. It authenticates with the
bot PAT (git-only path, no `gh` needed) — so it can *create* PRs but **cannot
approve** them. Verified on GPT-5.5: edit → commit → push → PR in ~1 min, no custom
tooling. (On the local qwen3-coder this needed a deterministic `open-pr` helper
crutch because the weak model fumbled the API call; on a frontier model the native
skill just works, so the crutch was removed.)

### Docs vault per repo

Each repo's `docs/` **is** an Obsidian vault — the durable knowledge layer
(architecture, decisions, phase plans, and the orchestration conventions in
`docs/agents/`). Because it lives *in the repo*, it serves all three readers at once:
you (Obsidian graph), the planner Claude (grounding), and the Hermes worker (context
in its clone, via `--skill obsidian`). Use **wikilinks inside the vault** — the
worker resolves them with the obsidian skill, and they're rename-safe with a richer
graph; use **standard markdown links only in README / GitHub-facing surfaces** (the
github.com UI doesn't render wikilinks). `tipped/docs` is the reference example: its
`docs/agents/` (orchestration-workflow, hermes-handoff-template, definition-of-done)
is the canonical per-repo convention set that `/handoff` defers to.

### Gotchas learned operationalizing this

- **`docker exec` runs as root (uid 0); the worker runs as uid 1001.** A repo cloned
  via `docker exec` is root-owned → the worker hits git "dubious ownership" and
  can't worktree it. The onboarder `chown -R 1001:1001` the workspace.
- **Model tier decides the final step.** The local qwen3-coder reliably *pushed* but
  couldn't reliably *open the PR* (fumbled the API call) — it needed a crutch. A
  frontier model (GPT-5.5) drives the native `github-pr-workflow` skill cleanly, so
  the crutch was removed. If you ever switch to a weaker backend, expect to re-add a
  deterministic PR helper.
- Branch protection has `enforce_admins: false` so you (admin) can still push
  directly when needed; the bot cannot (it's only a collaborator).

## Open decisions / next steps

- [ ] Stand up the bot GitHub account + fine-grained token; enable branch protection.
- [ ] Confirm Hermes' `messaging_platforms` / Telegram allowed-users schema in
      `config.yaml` against current docs (kept minimal in the template for now).
- [ ] Decide the sandbox execution model (see Docker-socket section).
- [ ] Wire the Claude reviewer loop on the spare laptop (`/loop` + `gh`).
- [ ] Optional: Dockge as a lightweight compose UI for non-Coolify containers.
