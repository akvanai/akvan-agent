# Changelog

All notable changes to Akvan Agent are documented in this file.

## [0.1.0] — 2026-07-03

### Added

- CLI chat loop with Hermes-style pinned prompt and multi-line input.
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
- Web tool backends: SearXNG, DuckDuckGo (DDGS), Firecrawl.
- Session cost tracking for providers that report dollar costs.

[0.1.0]: https://github.com/arash-dn/akvan-agent/releases/tag/v0.1.0
