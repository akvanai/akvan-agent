# Installing Akvan Agent

Akvan can be installed for the current user without root access. The installer
places its application copy and private Python environment in `~/.akvan`, then
creates the `akvan` command in `/usr/local/bin` for root installs or
`~/.local/bin` otherwise. When it uses `~/.local/bin`, it also adds that
directory to the user shell profile when needed.

Install Akvan with the website script or from a source checkout.

## Install or update

From our website (recommended):

```bash
curl -fsSL https://agent.akvan.app/install.sh | sh
```

From source (clone the repository, then run the installer from that tree):

```bash
git clone https://github.com/akvanai/akvan-agent.git
cd akvan-agent
./install.sh
```

The website script downloads a GitHub source archive and runs `./install.sh`
from the extracted tree. Override the version with `AKVAN_VERSION` (for example
`main` before the first release, or `v0.1.0` after tagging).

The installer bootstraps `uv` and Python when necessary, installs Akvan, syncs
bundled skills into `~/.akvan/skills/`, and opens first-time provider and model
configuration when run from an interactive terminal. If you install with
`curl | sh`, finish setup afterward with `akvan model`. Run the
same command later to update Akvan. The launcher preserves
the directory from which you run `akvan`, so project discovery and coding tools
operate on the intended project.

Bundled skills use `skills/<category>/<name>/SKILL.md` in the Akvan source tree.
Install/update copies them into `~/.akvan/skills/` while respecting local edits
and deletions. Re-sync manually with `akvan skills sync`. Opt out by creating
`~/.akvan/.no-bundled-skills`.

For non-root installs, if the current shell does not already include
`~/.local/bin`, the installer adds it to your shell profile. Open a new terminal
or run the PATH command printed by the installer to use `akvan` immediately.

## Data layout

After install, Akvan stores user data under `~/.akvan/` (override with
`AKVAN_HOME`):

```text
~/.akvan/
├── .env                 # API keys, provider, approval, web, gateway env
├── config.yaml          # memory, skills, logging, web, curator settings
├── state.db             # sessions, messages, FTS5 index, gateway prefs
├── memories/
│   ├── MEMORY.md        # agent notes (injected into system prompt)
│   └── USER.md          # user profile
├── knowledge/           # private global OKF bundle (index, log, concepts)
├── knowledge-state/     # curator cursor and pending knowledge proposals
├── skills/              # synced bundled + user/agent-created skills
├── logs/
│   ├── agent.log
│   ├── errors.log
│   └── gateway-{id}.log
├── SOUL.md              # optional agent identity (create manually)
├── approvals.json       # persistent "always allow" decisions
├── .no-bundled-skills   # opt-out marker for skill seeding
├── app/                 # installed source copy (installer-managed)
└── venv/                # private Python environment (installer-managed)
```

`~/.akvan/` is created with mode `0700`; session databases, logs, configuration,
memory, knowledge, and credential files are created with mode `0600`. See
[SECURITY.md](SECURITY.md) for details.

Project-level overrides: a `.env` in the project working directory merges with
`~/.akvan/.env`, and `.akvan/skills/` can override skills for that project.

## Remove Akvan

Remove the application and private Python environment while preserving skills,
`SOUL.md`, and other user data under `~/.akvan`. Managed Docker containers
deployed by `akvan tools` (SearXNG and browser runtime) are stopped and removed
as well.

```bash
akvan uninstall
```

Or with the installer:

```bash
./install.sh --uninstall
```

Remove the application and all Akvan user data:

```bash
akvan uninstall --purge
```

Or:

```bash
./install.sh --purge
```

Both uninstall paths remove managed Docker containers before deleting files.
Use `--yes` with `akvan uninstall` to skip confirmation in scripts.

Set `AKVAN_HOME` or `AKVAN_BIN_DIR` when running the installer to override the
default locations.
