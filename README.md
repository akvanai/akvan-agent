# Akvan Agent

[![GitHub release](https://img.shields.io/github/v/release/akvanai/akvan-agent?include_prereleases&label=release)](https://github.com/akvanai/akvan-agent/releases)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/akvanai/akvan-agent/ci.yml?branch=main&label=CI)](https://github.com/akvanai/akvan-agent/actions/workflows/ci.yml)

Akvan Agent is an agent harness with multi-provider support and a provider
abstraction layer. Use the interactive CLI for local sessions, or connect
gateways such as Telegram.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the package boundaries and extension points.

## Install

Install Akvan with the website script or from source:

From our website (recommended):

```bash
curl -fsSL https://agent.akvan.app/install.sh | sh
```

From source:

```bash
git clone https://github.com/akvanai/akvan-agent.git
cd akvan-agent
./install.sh
```

The installer checks for `uv`, installs it when needed, provides Python 3.12
when no compatible Python is available, installs Akvan into `~/.akvan`, creates
the `akvan` launcher in `/usr/local/bin` for root installs or `~/.local/bin`
otherwise, and opens the provider and model setup on the first
install when run from an interactive terminal. If setup is skipped because the
installer is piped through `sh`, run `akvan model` afterward. Run
the same command again to update an existing installation.

After it finishes, start Akvan with `akvan`.

See [INSTALL.md](INSTALL.md) for uninstall, purge, bundled skills, and the
[`~/.akvan/` data layout](INSTALL.md#data-layout).

## Logs

Akvan writes rotated log files under `~/.akvan/logs/`:

| File | Purpose |
|------|---------|
| `agent.log` | CLI activity (memory, skills, sessions, tools, errors) |
| `errors.log` | Warnings and errors only |
| `gateway-{id}.log` | Per-gateway process (e.g. `gateway-telegram.log`) |

View logs from the terminal:

```bash
akvan logs                  # last 50 lines of agent.log
akvan logs -f               # follow agent.log
akvan logs errors           # warnings and errors
akvan logs gateway telegram -f
akvan logs list             # show log files and sizes
akvan logs --since 1h --component memory
```

Tune logging in `~/.akvan/config.yaml` (`logging.level`, `logging.max_size_mb`,
`logging.backup_count`) or set `AKVAN_LOG_LEVEL=DEBUG` for troubleshooting.
Rotated backups keep disk use bounded (default: 5 MB × 3 per main log).

## Providers

Configure the active provider with `akvan model` or in `~/.akvan/.env`
(`AKVAN_PROVIDER`, `AKVAN_MODEL`). Supported providers:

- `openrouter` — uses `OPENROUTER_API_KEY`, loads the live OpenRouter model catalog, and uses the Chat Completions endpoint (`/chat/completions`).
- `openai-codex` — defaults to Codex CLI session discovery and can alternatively use `OPENAI_API_KEY`.
- `deepseek` — uses `DEEPSEEK_API_KEY` against the native DeepSeek API (`https://api.deepseek.com/v1`), with V4 thinking-mode support and `reasoning_content` replay for tool calls.
- `akvan` — sign in with OTP via `akvan model`, uses `AKVAN_API_KEY` and `AKVAN_BACKEND_URL` (default `https://agent.akvan.app`) against the Akvan backend proxy; billing uses Akvan plan credits instead of a direct provider key.

Akvan provider setup:

```bash
akvan model   # choose Akvan → enter email/phone → OTP → pick model
```

Or set manually in `~/.akvan/.env`:

```bash
AKVAN_PROVIDER=akvan
AKVAN_API_KEY=your_akvan_api_key
AKVAN_BACKEND_URL=https://agent.akvan.app
AKVAN_MODEL=openai/gpt-4o-mini
```

## Web tools

After install, Akvan exposes `web_extract` by default. `web_search` is optional and
requires a search backend. Configure search with:

```bash
akvan tools
```

| Tool | Purpose |
|------|---------|
| `web_search` | Search the web (up to 5 results with titles, URLs, descriptions) |
| `web_extract` | Fetch HTML page content as markdown (large pages summarized) |

**Extract backend:** built-in `content_extractor`, enabled on every install. It fetches
HTML pages directly and extracts paragraphs, headings, and tables.

**Search backends:** `searxng` or `ddgs` (DuckDuckGo; the wizard can `pip install ddgs`
into the Akvan venv, or install `akvan-agent[web]`). For SearXNG you can point at an
existing instance (`SEARXNG_URL`) or let `akvan tools` deploy a local single-container
SearXNG on `127.0.0.1` (no Redis required). Managed containers are removed when you
switch to ddgs/external search, switch browser runtime from Docker to local, or
when running `akvan uninstall` / `./install.sh --uninstall` / `--purge`.

Save search settings in `~/.akvan/.env` and/or `~/.akvan/config.yaml`
(`web.search_backend`). Extract uses the built-in backend by default; override with
`web.extract_backend` if needed. Optional `AKVAN_WEB_EXTRACT_SUMMARY_MODEL` selects
the model used to summarize large extracted pages. See
[config.yaml.example](config.yaml.example) and [.env.example](.env.example).

## Browser tools

Akvan can expose optional browser-backed tools for banner generation and X
account automation. They are off by default and must be enabled with:

```bash
akvan tools
```

Both tools share one `browser_runtime` backed by Playwright/Chromium. For X
account automation, local mode is bundled with Akvan Agent and auto-starts on
first use when the tool is enabled. Docker mode is managed by Akvan Agent: choose a port and Akvan starts its own
container, publishing it to the configured `browser_runtime.host` and
`browser_runtime.port`. Switching to local mode, or running `akvan uninstall` /
`./install.sh --uninstall` / `--purge`, stops and removes the Docker browser runtime
container. Expect roughly 2
CPU cores, 2 GB free RAM, and 1 GB disk for comfortable use. Docker mode may
need more memory.

Available optional toolsets:

| Toolset | Purpose |
|---------|---------|
| `banner_generation` | Create, inspect, and render reusable HTML/CSS/meta templates with Playwright |
| `x_account` | Check X auth, fetch profiles, and post only after explicit confirmation |

Banner generation keeps related files together under `~/.akvan/banners` by default: reusable
`templates/<id>/index.html`, `style.css`, and `meta.json`; generated PNGs under
`renders/`; and reusable local assets under `assets/`. Rendering substitutes escaped
data, disables page JavaScript, blocks browser network requests, and captures the
configured viewport with Playwright. X Playwright storage remains private. Never
commit `auth.json`, private media, or private templates.

## Chat, tools, and approvals

Type `/exit` or press `Ctrl-D` to quit. The prompt stays pinned at the bottom. `Enter` sends; `Esc` then `Enter` adds a new line. When the active provider reports a dollar cost, the prompt status row shows the accumulated cost for the current Akvan session.

CLI sessions are persisted to `~/.akvan/state.db`. Use `/sessions` to browse saved chats (15 per page) and `/resume <number>` to continue one from the list. The agent can also search past conversations with the `session_search` tool (FTS5 full-text search over stored messages). Type `/` for command suggestions.

Akvan includes direct `read_file`, `write_file`, and `patch` tools, plus local
`terminal` execution and owned background-process management through `process`.
The `memory` tool saves durable facts to `MEMORY.md` and `USER.md`; `session_search`
recalls past chats from the session database. Ordinary project edits run without prompts.
`read_file` can use absolute paths outside the project; it blocks known credential and
secret paths (`.env*`, `~/.ssh/`, `~/.akvan/.env`, etc.). The `terminal` tool is not
subject to the same read blocks and can bypass them — review approvals carefully.
Dangerous commands, sensitive file writes, and writes outside the project require
`once`, `session`, `always`, or `deny` approval. Approval times out closed after
60 seconds. Catastrophic host commands are always blocked, including in `--yolo` or
`/yolo` mode.

In the interactive terminal, approval requests appear inside the active response.
Press the displayed number (`1`–`4`) to choose, `y` to allow once, or `n`/`d`
to deny. If no choice is made before the configured timeout, Akvan denies the
operation and lets the model choose a safer next step.

Configure approvals with `AKVAN_APPROVAL_MODE`, `AKVAN_APPROVAL_TIMEOUT`, and
`AKVAN_TERMINAL_TIMEOUT`:

| Mode | Behavior |
|------|----------|
| `ask` (default) | Prompt for sensitive operations |
| `deny` | Auto-reject sensitive operations (no prompt) |
| `off` | Skip ordinary approvals (env-wide default; same effect as `--yolo`) |

Catastrophic host commands remain blocked in all modes, including `--yolo` and
`AKVAN_APPROVAL_MODE=off`.

## Persistent memory and session recall

Akvan remembers across sessions in two ways:

| Mechanism | Location | Purpose |
|-----------|----------|---------|
| **Curated memory** | `~/.akvan/memories/MEMORY.md`, `USER.md` | Compact facts always in the system prompt |
| **Session search** | `~/.akvan/state.db` (FTS5) | Full-text search over past conversations |
| **Global knowledge** | `~/.akvan/knowledge/` (OKF Markdown) | Detailed reusable facts, searched on demand |

- **MEMORY.md** — agent notes (environment, conventions, lessons learned)
- **USER.md** — user profile (preferences, communication style, expectations)

Both are shared across CLI and gateway sessions. At session start, their contents are
injected into the system prompt as a frozen snapshot. The agent updates them with the
`memory` tool (`add`, `replace`, `remove`, or batched `operations`). Mid-session writes
land on disk immediately but appear in the system prompt only after the next session or
`/reload`.

To find older discussions, the agent uses `session_search`: pass a `query` for keyword
discovery, `session_id` + `around_message_id` to scroll a transcript, or no args to
browse recent sessions.

Every N user turns (default 10), a **background review** runs automatically and may
save preferences or facts to memory without you asking. Every N tool iterations
(default 10), it may also update or create procedural skills via `skill_manage`.
These writes land on disk **directly** (no staging queue). Saved memory and loaded
skills are treated as trusted instructions in later sessions. Background review
cannot modify bundled skills. Set `nudge_interval: 0` or `creation_nudge_interval: 0`
to disable either dimension. Configure limits and notifications in `~/.akvan/config.yaml`:

Akvan also maintains an empty-by-default, private global knowledge bundle. Unlike compact
memory, knowledge is organized into dynamically created OKF concepts and is retrieved only
when relevant. Use `/knowledge` to see its status and `/knowledge pending` to review changes.
A separate curator runs every 15 persisted user turns by default. It may auto-save only
clear, safe facts quoted from the user; guesses, conflicts, sensitive facts, and deletions
require approval. Disable it with `knowledge.enabled: false`, or set
`knowledge.review_interval: 0` to keep the tools while disabling background review.

```yaml
memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200
  user_char_limit: 1375
  nudge_interval: 10          # background memory review; 0 = off

skills:
  creation_nudge_interval: 10 # background skill review; 0 = off

knowledge:
  enabled: true
  review_interval: 15         # background knowledge review; 0 = off
  auto_save_explicit_facts: true

display:
  review_notifications: on    # off | on | verbose
```

## Learning skills

Akvan can grow its procedural memory over time:

| Mechanism | How | Saves to |
|-----------|-----|----------|
| **`skill_manage` tool** | Agent creates/patches skills during tasks | `~/.akvan/skills/` |
| **`/learn …`** | Distill a workflow, URL, or directory into a skill | `~/.akvan/skills/` |
| **Background review** | Post-turn fork updates memory and skills | memory files + skills |

User-directed creates (including `/learn`) belong to you. Only skills created by the
background review fork are marked agent-created and eligible for curator archival.

### `/learn` source routing

Akvan classifies each `/learn` request and applies strict gather rules:

| Input | Source |
|-------|--------|
| `/learn` (no args) | Current session only; stops if the session is empty |
| `/learn … we just did / this conversation` | Current session turns |
| `/learn … from my last session` | `session_search` on past sessions (requires session store) |
| `/learn tailwind css` / general subjects | `web_search` + `web_extract` |
| `/learn our deploy pipeline` / project workflows | Current session, then `session_search` if thin |
| `/learn kubernetes` (could be either) | Asks whether to gather from sessions, web, or both |

```bash
/learn the deploy workflow we just ran
akvan skills curator status
akvan skills reset plan --restore   # restore a bundled skill from source
```

See [config.yaml.example](config.yaml.example) for a fuller sample including web tools.

## Telegram gateway

Chat with Akvan from Telegram DMs. The standard installer includes the optional
Telegram integration. Package users can install it with
`akvan-agent[telegram]`.

Open the gateway manager:

```bash
akvan gateway
```

From there you can configure Telegram, activate or deactivate the gateway, and run it
in the background without keeping a terminal open.

Or add to `~/.akvan/.env` manually:

```bash
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_ALLOWED_USERS=your_telegram_user_id
```

Create the bot via [@BotFather](https://t.me/BotFather). Find your user id with
[@userinfobot](https://t.me/userinfobot).

The gateway streams replies with Telegram drafts when available and falls back to
editing a single message. Its native menu provides `/new`, `/status`, `/settings`,
`/stop`, and `/help`; `/start` shows the welcome message. Authorized gateway users
have the same tool access as the local CLI (files, terminal, memory, skills). Sensitive
tool approvals use inline Allow once, Allow for session, optional Always allow, and
Deny buttons. `--yolo` and manual approval settings apply when configured.
Typing stays active while Akvan thinks, runs tools, or streams, and pauses while an
approval is waiting. Chat-scoped model, approval, and streaming preferences plus
sessions are persisted in `~/.akvan/state.db` without changing `.env`.

## Telegram delivery

Telegram delivery exposes tools that send content to an authorized Telegram user
after explicit confirmation:

- `telegram_send_file` — local files Telegram bots accept (images, PDFs, audio,
  video, archives, and other uploads; method chosen by MIME type)
- `telegram_send_text` — plain text messages
- `telegram_send_image` — image-only helper kept for compatibility

Configure it from the tools wizard:

```bash
akvan tools
```

Choose **Social Media → Telegram delivery**. Gateway chat and delivery can use
the same bot or different ones. If one side is already set up, setup asks
whether to reuse those credentials or configure separately.

Or add delivery-specific keys to `~/.akvan/.env`:

```bash
TELEGRAM_DELIVERY_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_DELIVERY_ALLOWED_USERS=your_telegram_user_id
```

If delivery keys are unset, Akvan falls back to the gateway `TELEGRAM_*` values
for backward compatibility. With one allowed user the destination is automatic;
with multiple allowed users the intended user ID must be supplied.

Telegram is one registered gateway integration. Future Slack, email, and web
integrations can supply their own adapters, configuration, access policies,
capabilities, and optional dependencies while reusing the same sessions,
commands, approvals, and streaming service. Each gateway runs in its own
background process; logs are written to `~/.akvan/logs/gateway-{id}.log`.

## Prompts and skills

Akvan builds one layered system prompt when the process starts. It uses `~/.akvan/SOUL.md` for optional identity, then frozen memory blocks from `MEMORY.md` and `USER.md` when enabled, runtime guidance, a compact skill index, project instructions, and session metadata. Project instructions prefer `.akvan.md` or `AKVAN.md`, then `AGENTS.md`.

### Skill layout

Akvan separates skill **content** from skill **code**:

- `skills/<category>/<name>/SKILL.md` at the repo root — bundled instruction packages shipped with Akvan
- `agent/skills/` — Python discovery engine (`registry.py`, `sync.py`, tools)

Every skill must use the categorized layout:

```text
skills/<category>/<name>/SKILL.md
```

Examples: `skills/creative/claude-design/SKILL.md`, `~/.akvan/skills/creative/claude-design/SKILL.md`.

### Runtime discovery

At runtime Akvan discovers skills from:

| Location | Purpose |
|----------|---------|
| `~/.akvan/skills/<category>/<name>/SKILL.md` | User skills (seeded on install + personal additions) |
| `.akvan/skills/<category>/<name>/SKILL.md` | Project overrides (wins on name collision) |

Install and update run `akvan skills sync`, which copies bundled skills from the app into `~/.akvan/skills/` while respecting local edits and deletions. Re-run manually with:

```bash
akvan skills sync
```

Opt out of bundled seeding by creating `~/.akvan/.no-bundled-skills`.

Use `/skills` to list skills by category, `/<skill-name> <request>` to activate one explicitly, and `/reload` to rebuild the cached prompt and skill snapshot. The system prompt contains only compact skill metadata. The agent uses `skills_list` for discovery and `skill_view` to load full instructions or a referenced text resource on demand. Skill resources are read-only; bundled scripts are never executed automatically.

## CLI reference

### Commands

| Command | Purpose |
|---------|---------|
| `akvan` | Interactive chat (default) |
| `akvan model` | Provider/model setup wizard |
| `akvan tools` | Optional web and browser-backed tools setup |
| `akvan gateway` | Gateway manager |
| `akvan gateway restart` | Restart running gateways to pick up code changes |
| `akvan logs …` | View and filter log files (see [Logs](#logs)) |
| `akvan skills sync` | Copy bundled skills into `~/.akvan/skills/` |
| `akvan skills reset <name>` | Reset bundled skill manifest tracking |
| `akvan skills reset <name> --restore` | Restore a bundled skill from source |
| `akvan skills curator status` | Show agent-created skill usage |
| `akvan skills curator archive` | Archive idle agent-created skills |
| `akvan skills curator restore <name>` | Restore an archived skill |
| `akvan skills curator pin\|unpin <name>` | Pin or unpin a skill |

### Global flags

| Flag | Applies to | Purpose |
|------|------------|---------|
| `--yolo` | `akvan`, `akvan gateway` | Skip ordinary approvals; catastrophic commands remain blocked |
| `--max-iterations N` | `akvan`, `akvan gateway` | Max agent iterations per user turn (default: 30) |
| `--model MODEL` | `akvan` | Override `AKVAN_MODEL` for this session |

### In-session slash commands

| Command | Purpose |
|---------|---------|
| `/exit`, `/quit` | Quit |
| `/reload` | Rebuild prompt and skills snapshot |
| `/skills` | List skills by category |
| `/usage` | Show estimated messages, tool-schema, output-reserve, and context usage |
| `/compress [focus]` | Compact old history while preserving the latest request and optional focus |
| `/sessions [page]` | Browse saved sessions (`next`, `prev`, or page number) |
| `/resume N` | Resume session number from `/sessions` |
| `/learn …` | Distill a workflow into a skill |
| `/yolo` | Toggle session approval bypass |
| `/<skill-name> …` | Activate a skill for one turn |

Akvan estimates the complete request before each provider call. Oversized tool
results are saved privately under `~/.akvan/tmp/tool-results/` and replaced in
context with a bounded preview and path. Long histories compact automatically,
and provider context-overflow errors trigger a bounded compact-and-retry cycle.
Large skill packages remain progressively disclosed: metadata is indexed first,
`SKILL.md` loads on demand, and supporting files load only when requested.

When stdout is not a TTY, Akvan runs a simpler prompt loop without the
pinned UI — useful for piping and scripting.

## Test

```bash
uv run pytest
```

## Provider Shape

All model calls go through a provider interface in `agent.providers.base`. OpenRouter is implemented in `agent.providers.openrouter`, and later providers can implement `Provider.complete(...)`, `Provider.stream_complete(...)`, and `Provider.list_models()`, then register their own setup handler.

## License

Akvan Agent is open source under the [MIT License](LICENSE).
