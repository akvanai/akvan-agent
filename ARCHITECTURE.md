# Akvan Agent Architecture

Akvan keeps orchestration at the package root and gives extensible concepts their own
packages. Dependencies point inward toward small contracts and never back toward the UI.

```text
UI ───────→ Session ───────→ Agent
Gateway ──→ Session ───────→ Agent
                │              │
                ├──→ Prompts   └──→ Tool contract
                ├──→ Skills ──────→ Skill tools
                └──→ Tool registry

Provider implementations ───→ Provider contract
```

## Package responsibilities

- `agent.py` runs turns, executes tool calls, and emits lifecycle events.
- `session.py` owns process-local history, prompt snapshots, tools, and reloads.
- `prompts/` discovers instructions and builds immutable prompt snapshots.
- `agent/skills/` parses `SKILL.md`, indexes metadata, syncs bundled skills, and exposes safe on-demand skill viewing.
- Repo-root `skills/<category>/<name>/` holds bundled instruction packages (content only).
- `tools/` defines the tool contract, result trust boundaries, and tool registry.
- `providers/` translates the provider-neutral contracts to model APIs.
- `ui/` owns terminal commands, setup, chat state, rendering, and setup wizards.
- `gateway/` owns platform-neutral messaging orchestration, contracts, session
  bindings, streaming delivery, registry lookup, and per-gateway daemons.
- `gateway/integrations/<id>/` owns each transport's adapter, configuration,
  dependency checks, authentication policy, and native rendering.
- `cli.py` is intentionally only the console-script entry point.

Package `__init__.py` files contain public re-exports only. Shared behavior should not be
placed in generic `core` or `utils` packages; put it with the concept that owns it.

## Adding features

- Add a provider by implementing `providers.base.Provider`.
- Add a built-in tool in `tools/` and register it in `tools/registry.py`.
- Add skill behavior in `agent/skills/`; bundled instruction packages live in repo-root `skills/<category>/<name>/` and are seeded into `~/.akvan/skills/` on install.
- Add terminal behavior in `ui/` without importing UI modules into session, agent, prompts,
  skills, tools, or providers.
- Add a messaging gateway by implementing `GatewayIntegration` and
  `GatewayAdapter` under `gateway/integrations/<id>/`, then register the
  integration in `gateway/registry.py`. The shared service and setup UI should
  not need transport-specific branches.

## Gateway extension contract

Each integration supplies stable metadata, configuration load/save/validation,
an optional-dependency check, an access policy, runtime delivery tuning, safe
summary rows, and an adapter factory. Adapters normalize inbound events into
`InboundMessage`/`CallbackInteraction` and declare capabilities such as
buttons, callbacks, message editing, typing, draft streaming, and message size.

Gateways run in separate processes selected with `--gateway-id`. PID and log
files are namespaced by gateway id, while session bindings and preferences share
the existing SQLite store under `(platform, chat_id)`.
