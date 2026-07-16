"""Shared file read safety rules for tool adapters.

**This is NOT a security boundary.** The terminal tool runs as the same OS user
with shell access; the agent can still ``cat ~/.ssh/id_ed25519`` or read
``~/.akvan/.env`` via shell. The read denylist exists as defense-in-depth:

  * Returns a clear error to models that respect tool denials.
  * Surfaces an audit trail when something tries to read credentials.

Treat any user-visible framing as "may help" rather than "stops attackers."
"""

from __future__ import annotations

import os
from pathlib import Path

_BLOCKED_ENV_BASENAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".env.staging",
        ".env.test",
        ".envrc",
    }
)

_AKVAN_CREDENTIAL_FILES: tuple[str, ...] = (
    ".env",
    "approvals.json",
)

_SYSTEM_SENSITIVE_FILES: frozenset[str] = frozenset(
    {
        "/etc/shadow",
        "/etc/sudoers",
    }
)


def _akvan_home_path() -> Path:
    try:
        from agent.config import akvan_home

        return akvan_home()
    except Exception:
        return Path.home() / ".akvan"


def _home_credential_prefixes(home: Path) -> tuple[str, ...]:
    return tuple(
        str((home / name).resolve()) + os.sep
        for name in (
            ".ssh",
            ".aws",
            ".gnupg",
            ".kube",
            ".docker",
            ".azure",
            ".config/gh",
            ".config/gcloud",
        )
    )


def _home_credential_files(home: Path) -> frozenset[str]:
    paths = {
        str((home / ".ssh" / name).resolve())
        for name in ("authorized_keys", "id_rsa", "id_ed25519", "config")
    }
    for name in (".netrc", ".pgpass", ".npmrc", ".pypirc", ".git-credentials"):
        paths.add(str((home / name).resolve()))
    return frozenset(paths)


def _akvan_credential_files(akvan_home: Path) -> frozenset[str]:
    return frozenset(
        str((akvan_home / name).resolve()) for name in _AKVAN_CREDENTIAL_FILES
    )


def build_write_denied_paths(home: str) -> set[str]:
    """Return exact sensitive paths that must never be written (future reuse)."""
    home_path = Path(home).expanduser().resolve()
    akvan_home = _akvan_home_path().resolve()
    denied = set(_home_credential_files(home_path))
    denied.update(_akvan_credential_files(akvan_home))
    denied.update(_SYSTEM_SENSITIVE_FILES)
    return denied


def get_read_block_error(path: str | Path) -> str | None:
    """Return an error message when ``read_file`` must refuse ``path``.

    Callers that resolve relative paths against a project root MUST pass the
    resolved absolute path. This function's own ``resolve()`` is anchored at
    the process cwd for relative inputs.

    Reads outside the project root are allowed unless they hit a sensitive
    category below. Reads under ``~/.akvan/skills/`` are always allowed.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return None

    resolved_str = str(resolved)
    home = Path.home().resolve()
    akvan_home = _akvan_home_path().resolve()

    # Skills are meant to be loaded by the agent.
    try:
        skills_root = (akvan_home / "skills").resolve()
        resolved.relative_to(skills_root)
        return None
    except ValueError:
        pass

    if resolved_str in _SYSTEM_SENSITIVE_FILES:
        return (
            f"Access denied: {path} is a sensitive system file and cannot "
            "be read directly. (Defense-in-depth — not a security boundary; "
            "the terminal tool can still bypass.)"
        )

    if resolved_str in _home_credential_files(home):
        return (
            f"Access denied: {path} is a credential file and cannot be read "
            "directly to prevent credential leakage. "
            "(Defense-in-depth — not a security boundary; the terminal tool "
            "can still bypass.)"
        )

    for prefix in _home_credential_prefixes(home):
        if resolved_str.startswith(prefix):
            return (
                f"Access denied: {path} is under a sensitive credential "
                "directory and cannot be read directly. "
                "(Defense-in-depth — not a security boundary; the terminal "
                "tool can still bypass.)"
            )

    for blocked in _akvan_credential_files(akvan_home):
        if resolved_str == blocked:
            return (
                f"Access denied: {path} is an Akvan credential store and "
                "cannot be read directly. "
                "(Defense-in-depth — not a security boundary; the terminal "
                "tool can still bypass.)"
            )

    if resolved.name in _BLOCKED_ENV_BASENAMES:
        return (
            f"Access denied: {path} is a secret-bearing environment file "
            "and cannot be read to prevent credential leakage. "
            "If you need to check the file structure, read .env.example "
            "instead. (Defense-in-depth — not a security boundary; the "
            "terminal tool can still bypass.)"
        )

    return None
