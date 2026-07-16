# Akvan Agent Architecture

Akvan keeps orchestration at the package root and gives extensible concepts their own
packages. Dependencies point inward toward small contracts and never back toward the UI.

```text
UI ───────→ Session ───────→ Agent
Gateway ──→ Session ───────→ Agent
                │              │
                ├──→ Prompts   └──→ Tool contract
                ├──→ Skills ──────→ Skill tools
                ├──→ Memory ──────→ memory tool
                ├──→ Knowledge ───→ knowledge tools
                ├──→ Storage ─────→ session_search (FTS5)
                └──→ Tool registry

Provider implementations ───→ Provider contract
```

## Package responsibilities

- `agent.py` runs turns, executes tool calls, and emits lifecycle events.
- `session/` owns process-local history via three coordinators — `PromptCoordinator`
  (frozen snapshots, memory-backed prompt inputs), `ToolCoordinator` (registry,
  approvals, resolved tools), and `PersistenceCoordinator` (SQLite identity and
  message sync). `AgentSession` is a slim facade for factory wiring, cross-coordinator
  `reload()`, and turn-level learning hooks.
- `prompts/` discovers instructions and builds immutable prompt snapshots.
- `agent/skills/` parses `SKILL.md`, indexes metadata, syncs bundled skills, exposes
  read tools (`skills_list`, `skill_view`) and write tool (`skill_manage`), and runs
  background skill review plus curator maintenance for agent-created skills.
- Repo-root `skills/<category>/<name>/` holds bundled instruction packages (content only).
- `tools/` defines the tool contract, result trust boundaries, and tool registry.
- `providers/` translates the provider-neutral contracts to model APIs.
- `ui/` owns terminal commands, setup, chat state, rendering, and setup wizards.
- `gateway/` owns platform-neutral messaging orchestration via four coordinators —
  `CommandService` (slash commands and settings menus), `ApprovalFlowService`
  (interactive tool approvals), `ChatSessionService` (chat-to-session bindings and
  turn lifecycle), and `DeliveryService` (outbound adapter I/O and streaming).
  `GatewayService` is a thin router; `bindings.py`, `stream_consumer.py`,
  contracts, registry lookup, and per-gateway daemons support the stack.
- `memory/` owns persistent curated memory (`MEMORY.md`, `USER.md`), background
  review, and memory configuration.
- `knowledge/` owns private global OKF concepts, search, proposals, validation,
  generated indexes/logs, and the dedicated conversation curator.
- `storage/` persists session metadata and message history to SQLite
  (`~/.akvan/state.db`), including FTS5 full-text search for `session_search`.
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
- Enable persistent memory via the `memory` toolset (`memory` tool) and past
  session recall via the `sessions` toolset (`session_search` tool). Configure
  limits and background review in `~/.akvan/config.yaml` under `memory:`.
- Enable global knowledge via the `knowledge` toolset. It starts empty under
  `~/.akvan/knowledge/`; configure its dedicated review under `knowledge:`.

## Memory

Akvan has two complementary recall layers:

| Layer | Storage | How it is used |
|-------|---------|----------------|
| **Curated memory** | `~/.akvan/memories/MEMORY.md`, `USER.md` | Injected into the system prompt at session start (frozen snapshot) |
| **Session search** | `~/.akvan/state.db` (FTS5 index) | On-demand via the `session_search` tool |

**Frozen snapshot:** Memory blocks are captured once when the session starts. Mid-session
`memory` tool writes persist to disk immediately but do not change the live system prompt
until the next session or `/reload`. This keeps the prefix cache stable.

**Background review:** After every N user turns (default 10), a daemon thread replays the
conversation with a memory-only tool whitelist and may auto-save facts to `MEMORY.md` or
`USER.md`. Wired from `ui/chat.py` and `gateway/chat_session.py` via `AgentSession.maybe_spawn_background_review()`.

**Thread safety:** `SessionStore` opens SQLite with `check_same_thread=False` and serializes
all access through a `threading.Lock`, so tools like `session_search` work from the TUI
background thread and gateway worker threads.

## Knowledge

Knowledge complements memory and skills: memory stores compact personal facts,
skills store reusable procedures, and knowledge stores detailed facts about subjects
that matter to the user. The main agent searches and reads concepts on demand, so the
bundle is not injected into the frozen system prompt.

Akvan accepts permissive OKF v0.1 documents and writes a stronger profile containing
`type`, `title`, `description`, `timestamp`, confidence, and conversation sources. A
dedicated curator reviews persisted conversations every 15 user turns by default.
Verified safe user statements may be applied automatically; inference, conflicts,
sensitive changes, and destructive changes remain pending until the user approves them.

## Gateway extension contract

Each integration supplies stable metadata, configuration load/save/validation,
an optional-dependency check, an access policy, runtime delivery tuning, safe
summary rows, and an adapter factory. Adapters normalize inbound events into
`InboundMessage`/`CallbackInteraction` and declare capabilities such as
buttons, callbacks, message editing, typing, draft streaming, and message size.

Gateways run in separate processes selected with `--gateway-id`. PID and log
files are namespaced by gateway id, while session bindings and preferences share
the existing SQLite store under `(platform, chat_id)`.
