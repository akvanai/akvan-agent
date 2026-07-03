# Installing Akvan Agent

Akvan can be installed for the current user without root access. The installer
places its application copy and private Python environment in `~/.akvan`, then
creates the command at `~/.local/bin/akvan`.

## Install or update

From the Akvan source directory, run one command:

```bash
./install.sh
```

The installer bootstraps `uv` and Python when necessary, installs Akvan, syncs
bundled skills into `~/.akvan/skills/`, and opens first-time provider and model
configuration. Run the same command later to update Akvan. The launcher preserves
the directory from which you run `akvan`, so project discovery and coding tools
operate on the intended project.

Bundled skills use `skills/<category>/<name>/SKILL.md` in the Akvan source tree.
Install/update copies them into `~/.akvan/skills/` while respecting local edits
and deletions. Re-sync manually with `akvan skills sync`. Opt out by creating
`~/.akvan/.no-bundled-skills`.

If the installer reports that `~/.local/bin` is not on `PATH`, add it once to
your shell profile or open a shell that already includes the standard user bin
directory.

## Remove Akvan

Remove the application and private Python environment while preserving skills,
`SOUL.md`, and other user data under `~/.akvan`:

```bash
./install.sh --uninstall
```

Remove the application and all Akvan user data:

```bash
./install.sh --purge
```

Set `AKVAN_HOME` or `AKVAN_BIN_DIR` when running the installer to override the
default locations.
