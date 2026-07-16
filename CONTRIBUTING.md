# Contributing to Akvan Agent

Thanks for your interest in contributing! Akvan Agent is an open-source Python
harness agent, and contributions are welcome.

## Getting Started

1. Fork the repository and clone it locally.
2. Install [uv](https://docs.astral.sh/uv/).
3. Run `uv sync --all-extras` to install the project and dev dependencies.
4. Run `uv run pytest` to confirm everything passes.

## Development Workflow

- Create a feature branch from `main`.
- Follow the existing code style — no separate formatter config; match what's there.
- Write tests for new functionality. Tests live under `tests/` mirroring the package structure.
- Run `uv run pytest` before opening a PR.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for package boundaries and extension points.
The key principle: dependencies point inward toward small contracts and never back
toward the UI.

### Adding a Provider

Implement `agent.providers.base.Provider` and register it. See `openrouter.py`,
`deepseek.py`, or `openai_codex.py` for examples.

### Adding a Tool

Add the tool in `agent/tools/` and register it in `agent/tools/registry.py`.
Tools return `ToolResult` and may attach an `ApprovalPolicy` for sensitive operations.

### Adding a Skill

Create `skills/<category>/<name>/SKILL.md` at the repo root. The README documents
the layout rules. Skills are content-only — no executable code.

### Adding a Gateway Integration

Implement the contracts in `agent/gateway/contracts.py` and register in
`agent/gateway/registry.py`. See `integrations/telegram/` for the reference.

## Pull Requests

- Keep PRs focused on a single change.
- Reference related issues.
- Add or update tests for your changes.
- Ensure all tests pass before requesting review.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
By participating, you agree to abide by its terms.

## Questions?

Open a [GitHub Discussion](https://github.com/akvanai/akvan-agent/discussions) or
file an issue.
