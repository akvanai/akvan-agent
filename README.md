# Akvan Agent

[![PyPI version](https://img.shields.io/pypi/v/akvan-agent?color=blue)](https://pypi.org/project/akvan-agent/)
[![Python](https://img.shields.io/pypi/pyversions/akvan-agent)](https://pypi.org/project/akvan-agent/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![CI](https://github.com/arash-dn/akvan-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/arash-dn/akvan-agent/actions/workflows/ci.yml)

Akvan Agent is a small Python agent harness with a CLI chat loop and a provider abstraction. The first provider is OpenRouter.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the package boundaries and extension points.

## Install

```bash
./install.sh
```

That is the entire setup. The installer checks for `uv`, installs it when
needed, provides Python 3.12 when no compatible Python is available, installs
Akvan into `~/.akvan`, creates `~/.local/bin/akvan`, and opens the provider
and model setup on the first install. Run the same command again to update an
existing installation.

After it finishes, start Akvan with `akvan`.

Supported providers:

- `openrouter` — uses `OPENROUTER_API_KEY`, loads the live OpenRouter model catalog, and uses the Chat Completions endpoint (`/chat/completions`).
- `openai-codex` — defaults to Codex CLI session discovery and can alternatively use `OPENAI_API_KEY`.
- `deepseek` — uses `DEEPSEEK_API_KEY` against the native DeepSeek API (`https://api.deepseek.com/v1`), with V4 thinking-mode support and `reasoning_content` replay for tool calls.

Type `/exit` or press `Ctrl-D` to quit. The prompt stays pinned at the bottom, Hermes-style. `Enter` sends; `Esc` then `Enter` adds a new line. When the active provider reports a dollar cost, the prompt status row shows the accumulated cost for the current Akvan session.

CLI sessions are persisted to `~/.akvan/state.db`. Use `/sessions` to browse saved chats (15 per page) and `/resume <number>` to continue one from the list. Type `/` for command suggestions.

Akvan includes direct `read_file`, `write_file`, and `patch` tools, plus local
`terminal` execution and owned background-process management through `process`.
Ordinary project edits run without prompts. Dangerous commands, sensitive files,
and writes outside the project require `once`, `session`, `always`, or `deny`
approval. Approval times out closed after 60 seconds. Catastrophic host commands
are always blocked, including in `--yolo` or `/yolo` mode.

In the interactive terminal, approval requests appear inside the active response.
Press the displayed number (`1`–`4`) to choose, `y` to allow once, or `n`/`d`
to deny. If no choice is made before the configured timeout, Akvan denies the
operation and lets the model choose a safer next step.

Configure this with `AKVAN_APPROVAL_MODE`, `AKVAN_APPROVAL_TIMEOUT`, and
`AKVAN_TERMINAL_TIMEOUT`.

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
`/stop`, and `/help`; `/start` shows the welcome message. Sensitive tool approvals
use inline Allow once, Allow for session, optional Always allow, and Deny buttons.
Typing stays active while Akvan thinks, runs tools, or streams, and pauses while an
approval is waiting. Chat-scoped model, approval, and streaming preferences plus
sessions are persisted in `~/.akvan/state.db` without changing `.env`.

Telegram is one registered gateway integration. Future Slack, email, and web
integrations can supply their own adapters, configuration, access policies,
capabilities, and optional dependencies while reusing the same sessions,
commands, approvals, and streaming service. Each gateway runs in its own
background process and log.

## Prompts and skills

Akvan builds one layered system prompt when the process starts. It uses `~/.akvan/SOUL.md` for optional identity, then runtime guidance, a compact skill index, project instructions, and session metadata. Project instructions prefer `.akvan.md` or `AKVAN.md`, then `AGENTS.md`.

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

## Test

```bash
uv run pytest
```

## Provider Shape

All model calls go through a provider interface in `agent.providers.base`. OpenRouter is implemented in `agent.providers.openrouter`, and later providers can implement `Provider.complete(...)`, `Provider.stream_complete(...)`, and `Provider.list_models()`, then register their own setup handler.

## License

Akvan Agent is open source under the [MIT License](LICENSE).
