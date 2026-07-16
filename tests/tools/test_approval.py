from __future__ import annotations

import stat
from pathlib import Path

from agent.tools.approval import (
    ApprovalChoice,
    ApprovalLevel,
    ApprovalManager,
    ApprovalRequirement,
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
