# Changelog

All notable changes to Akvan Agent are documented in this file.

## [0.2.0] - 2026-07-23

### Added

- **Vision support** — native multimodal images or auxiliary text descriptions,
  depending on provider/model capability (`AKVAN_VISION_MODE=auto|native|aux|off`).
- **`vision_analyze` tool** — analyze a local image path or `http(s)` URL; pixels
  are attached on vision-capable models, otherwise an aux vision model describes them.
- **Telegram image input** — photos/documents downloaded and passed into the agent turn.
- **CLI image paths** — extract local image paths from user text and attach them to the turn.
- Browser screenshots and banner generation results can attach images into the model loop.
- OpenAI Codex provider multimodal content formatting for vision turns.
- Context compression prunes historical image parts; multimodal tool results keep images
  until compaction.
- Config: `AKVAN_AUX_VISION_MODEL` (default `openai/gpt-4o-mini`) and optional
  `AKVAN_MODEL_SUPPORTS_VISION` override.

## [0.1.0] - Unreleased

### Added

- Provider abstraction with three implementations:
  - **OpenRouter** — live model catalog, Chat Completions API.
  - **OpenAI Codex** — CLI session discovery or API key authentication.
  - **DeepSeek** — native API with V4 thinking-mode and `reasoning_content` replay.
- Built-in tools: `read_file`, `write_file`, `patch`, `terminal`, `process`, `web_search`, `web_extract`.
- Approval system with `ask`/`deny`/`off` modes, 60-second timeout, and `once`/`session`/`always` policies.
- Session persistence via SQLite (`~/.akvan/state.db`) with `/sessions` and `/resume`.
- Telegram gateway with streaming drafts, inline approvals, and background daemon.
- Gateway extension contract for Slack, email, web, and other transports.
- Skill system with categorized layout, runtime discovery, sync command, and 10 bundled skills:
  - Creative: architecture-diagram, claude-design, design-md, popular-web-designs
  - GitHub: github-auth, github-code-review, github-pr-workflow
  - Software Development: plan, spike, systematic-debugging, test-driven-development
- Layered system prompt with `SOUL.md` support, project instructions, and skill index.
- First-time setup wizard and `akvan model` configuration command.
- Installer with `uv` bootstrapping, Python provisioning, and `--uninstall`/`--purge`.
- Web tool backends: SearXNG, DuckDuckGo (DDGS), built-in content extractor.
- Session cost tracking for providers that report dollar costs.
- **Self-learning skills** — `skill_manage` tool for create/patch/edit/delete of user
  skills under `~/.akvan/skills/`, `/learn` slash command for user-directed skill
  authoring, and combined background memory+skill review after turns.
- **Skill curator** — usage tracking in `~/.akvan/skills/.usage.json`, with
  `akvan skills curator status|archive|restore|pin|unpin` for agent-created skills.
- **`akvan skills reset`** — reset bundled skill manifest tracking or restore from source.
- **Persistent memory** — `MEMORY.md` and `USER.md` under `~/.akvan/memories/`, managed
  via the `memory` tool and injected into the system prompt at session start.
- **`session_search` tool** — FTS5 full-text search over past sessions in
  `~/.akvan/state.db` (discover, scroll, and browse modes).
- **Background memory review** — periodic post-turn fork that auto-saves user
  preferences and durable facts (configurable via `memory.nudge_interval`).
- Memory configuration in `~/.akvan/config.yaml` (`memory:` and `display.memory_notifications`).
- SQLite schema v4 with `messages_fts` virtual table and migration backfill.

### Fixed

- `session_search` SQLite threading error when tools run from the TUI background thread
  or gateway worker (`check_same_thread=False` with lock serialization).
