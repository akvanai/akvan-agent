from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agent.tools.approval import (
    ApprovalChoice,
    ApprovalLevel,
    ApprovalManager,
    ApprovalRequirement,
    classify_file_write,
    classify_terminal,
)


def requirement() -> ApprovalRequirement:
    return ApprovalRequirement(
        ApprovalLevel.ASK,
        "dangerous operation",
        "test risk",
        "test:key",
    )


def test_once_session_and_noninteractive_decisions(tmp_path: Path) -> None:
    manager = ApprovalManager(user_home=tmp_path)
    pending = manager.prepare("terminal", requirement())
    assert pending.request is not None
    denied = manager.resolve(pending.request, requirement())
    assert not denied.allowed

    manager.set_callback(lambda request, timeout: ApprovalChoice.SESSION)
    pending = manager.prepare("terminal", requirement())
    assert pending.request is not None
    assert manager.resolve(pending.request, requirement()).allowed
    assert manager.prepare("terminal", requirement()).allowed


def test_permanent_approval_is_persisted_with_restricted_mode(
    tmp_path: Path,
) -> None:
    manager = ApprovalManager(user_home=tmp_path)
    manager.set_callback(lambda request, timeout: ApprovalChoice.ALWAYS)
    pending = manager.prepare("terminal", requirement())
    assert pending.request is not None
    assert manager.resolve(pending.request, requirement()).allowed

    fresh = ApprovalManager(user_home=tmp_path)
    assert fresh.prepare("terminal", requirement()).allowed
    assert stat.S_IMODE(fresh.approvals_path.stat().st_mode) == 0o600


def test_hard_blocklist_cannot_be_bypassed_by_yolo(tmp_path: Path) -> None:
    manager = ApprovalManager(user_home=tmp_path, yolo=True)
    blocked = classify_terminal(
        "rm -rf /", workdir=tmp_path, project_root=tmp_path
    )
    result = manager.prepare("terminal", blocked)

    assert blocked is not None
    assert blocked.level == ApprovalLevel.BLOCK
    assert not result.allowed
    assert result.request is None


def test_yolo_bypasses_ordinary_approval(tmp_path: Path) -> None:
    manager = ApprovalManager(user_home=tmp_path, yolo=True)
    assert manager.prepare("terminal", requirement()).allowed


def test_vault_file_write_skips_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".akvan"
    monkeypatch.setenv("AKVAN_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    vault_file = home / "vault" / "shot.png"

    assert classify_file_write(vault_file, project_root=project) is None


def test_non_vault_akvan_file_write_still_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".akvan"
    monkeypatch.setenv("AKVAN_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()

    env_req = classify_file_write(home / ".env", project_root=project)
    assert env_req is not None
    assert env_req.level == ApprovalLevel.ASK

    profile = home / "browser" / "profiles" / "x" / "storage_state.json"
    profile_req = classify_file_write(profile, project_root=project)
    assert profile_req is not None
    assert profile_req.level == ApprovalLevel.ASK


def test_terminal_vault_redirect_not_sensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".akvan"
    monkeypatch.setenv("AKVAN_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()

    vault_cmd = classify_terminal(
        f"cat shot.png > {home}/vault/shot.png",
        workdir=project,
        project_root=project,
    )
    assert vault_cmd is None

    env_cmd = classify_terminal(
        f"echo secret > {home}/.env",
        workdir=project,
        project_root=project,
    )
    assert env_cmd is not None
    assert "write to a sensitive path" in env_cmd.reason
