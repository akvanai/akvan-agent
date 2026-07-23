"""Central approval policy for mutating tools."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from agent.storage.permissions import (
    ensure_private_dir,
    ensure_private_file,
    replace_private_file,
)


class ApprovalChoice(str, Enum):
    ONCE = "once"
    SESSION = "session"
    ALWAYS = "always"
    DENY = "deny"


class ApprovalLevel(str, Enum):
    ASK = "ask"
    BLOCK = "block"


@dataclass(frozen=True)
class ApprovalRequirement:
    level: ApprovalLevel
    summary: str
    reason: str
    key: str
    allow_permanent: bool = True


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    tool_name: str
    summary: str
    reason: str
    choices: tuple[ApprovalChoice, ...]


@dataclass(frozen=True)
class ApprovalResult:
    allowed: bool
    message: str = ""
    request: ApprovalRequest | None = None


ApprovalCallback = Callable[[ApprovalRequest, int], ApprovalChoice | str]


_HARD_PATTERNS = tuple(
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), reason)
    for pattern, reason in (
        (r"\brm\s+(?:-[^\s]+\s+)*(?:/|/\*|~|\$HOME)(?:\s|$)", "recursive deletion of a protected root"),
        (r"\bmkfs(?:\.[a-z0-9]+)?\b", "filesystem formatting"),
        (r"\bdd\b[^\n]*\bof=/dev/(?:sd|nvme|hd|mmcblk|vd|xvd)", "raw block-device write"),
        (r">\s*/dev/(?:sd|nvme|hd|mmcblk|vd|xvd)", "raw block-device write"),
        (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
        (r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:shutdown|reboot|poweroff|halt)\b", "host shutdown or reboot"),
    )
)

_DANGEROUS_PATTERNS = tuple(
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), reason)
    for pattern, reason in (
        (r"\brm\s+[^\n]*(?:-[^\s]*r[^\s]*|--recursive)", "recursive file deletion"),
        (r"\bgit\s+(?:reset\s+--hard|clean\s+-[^\s]*f|push\s+[^\n]*--force)", "destructive Git operation"),
        (r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b", "download piped to a shell"),
        (r"\bchmod\s+(?:-[^\s]+\s+)*777\b", "world-writable permissions"),
        (r"\b(?:sudo|su)\b", "privilege escalation"),
        (r"\bkill\s+(?:-[^\s]+\s+)*(?:-1|1)\b", "system-wide or init process termination"),
        (
            r"(?:>|>>|\btee\b|\bsed\s+-i\b)[^\n]*(?:/etc/|/\.ssh/|/\.akvan/(?!vault(?:/|$))|\.env\b)",
            "write to a sensitive path",
        ),
    )
)


def classify_terminal(command: str, *, workdir: Path, project_root: Path) -> ApprovalRequirement | None:
    normalized = command.strip()
    for pattern, reason in _HARD_PATTERNS:
        if pattern.search(normalized):
            return ApprovalRequirement(
                ApprovalLevel.BLOCK, normalized, reason, f"hard:{reason}", False
            )
    reasons = [
        reason for pattern, reason in _DANGEROUS_PATTERNS
        if pattern.search(normalized)
    ]
    try:
        workdir.resolve().relative_to(project_root.resolve())
    except ValueError:
        reasons.append("command runs outside the project root")
    if not reasons:
        return None
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return ApprovalRequirement(
        ApprovalLevel.ASK,
        normalized,
        "; ".join(dict.fromkeys(reasons)),
        f"terminal:{digest}",
        True,
    )


def classify_file_write(path: Path, *, project_root: Path) -> ApprovalRequirement | None:
    from agent.vault import is_under_vault

    resolved = path.resolve(strict=False)
    if is_under_vault(resolved):
        return None
    reasons: list[str] = []
    try:
        relative = resolved.relative_to(project_root.resolve())
    except ValueError:
        relative = None
        reasons.append("write is outside the project root")
    sensitive_names = {".env", "config.yaml", "config.yml", "approvals.json"}
    if (
        resolved.name in sensitive_names or resolved.name.startswith(".env.")
        or ".ssh" in resolved.parts
        or ".akvan" in resolved.parts
    ):
        reasons.append("target is a sensitive configuration or credential path")
    if not reasons:
        return None
    key_path = str(relative if relative is not None else resolved)
    digest = hashlib.sha256(key_path.encode("utf-8")).hexdigest()
    return ApprovalRequirement(
        ApprovalLevel.ASK,
        f"write {resolved}",
        "; ".join(dict.fromkeys(reasons)),
        f"file:{digest}",
        relative is not None,
    )


class ApprovalManager:
    """Session decisions plus a persistent exact-key allowlist."""

    def __init__(
        self,
        *,
        mode: str = "ask",
        timeout: int = 60,
        user_home: Path | None = None,
        yolo: bool = False,
    ) -> None:
        if mode not in {"ask", "deny", "off"}:
            raise ValueError("approval mode must be ask, deny, or off")
        self.mode = mode
        self.timeout = max(1, timeout)
        self.user_home = (user_home or Path.home()).resolve()
        self.yolo = yolo
        self._session_keys: set[str] = set()
        self._callback: ApprovalCallback | None = None
        self._lock = threading.Lock()

    @property
    def approvals_path(self) -> Path:
        return self.user_home / ".akvan" / "approvals.json"

    def set_callback(self, callback: ApprovalCallback | None) -> None:
        self._callback = callback

    def toggle_yolo(self) -> bool:
        self.yolo = not self.yolo
        return self.yolo

    def prepare(
        self, tool_name: str, requirement: ApprovalRequirement | None
    ) -> ApprovalResult:
        if requirement is None:
            return ApprovalResult(True)
        if requirement.level == ApprovalLevel.BLOCK:
            return ApprovalResult(
                False, f"Blocked unconditionally: {requirement.reason}"
            )
        if self.yolo or self.mode == "off":
            return ApprovalResult(True)
        if (
            requirement.key in self._session_keys
            or requirement.key in self._load_permanent()
        ):
            return ApprovalResult(True)
        if self.mode == "deny":
            return ApprovalResult(
                False, f"Approval denied by policy: {requirement.reason}"
            )
        choices = [ApprovalChoice.ONCE, ApprovalChoice.SESSION]
        if requirement.allow_permanent:
            choices.append(ApprovalChoice.ALWAYS)
        choices.append(ApprovalChoice.DENY)
        request = ApprovalRequest(
            uuid.uuid4().hex,
            tool_name,
            requirement.summary,
            requirement.reason,
            tuple(choices),
        )
        return ApprovalResult(False, request=request)

    def resolve(
        self, request: ApprovalRequest, requirement: ApprovalRequirement
    ) -> ApprovalResult:
        if self._callback is None:
            return ApprovalResult(
                False,
                "Approval required, but no interactive approval channel is available.",
            )
        try:
            raw = self._callback(request, self.timeout)
            choice = (
                raw if isinstance(raw, ApprovalChoice) else ApprovalChoice(raw)
            )
        except Exception:
            return ApprovalResult(
                False, "Approval prompt failed or timed out; operation denied."
            )
        if choice == ApprovalChoice.DENY:
            return ApprovalResult(False, "User denied the operation.")
        if choice == ApprovalChoice.SESSION:
            self._session_keys.add(requirement.key)
        elif choice == ApprovalChoice.ALWAYS:
            if not requirement.allow_permanent:
                return ApprovalResult(
                    False, "Permanent approval is not allowed for this operation."
                )
            self._save_permanent(requirement.key)
        return ApprovalResult(True)

    def _load_permanent(self) -> set[str]:
        try:
            data = json.loads(
                self.approvals_path.read_text(encoding="utf-8")
            )
            keys = data.get("keys", []) if isinstance(data, dict) else []
            return {item for item in keys if isinstance(item, str)}
        except (OSError, ValueError, TypeError):
            return set()

    def _save_permanent(self, key: str) -> None:
        with self._lock:
            keys = self._load_permanent()
            keys.add(key)
            path = self.approvals_path
            ensure_private_dir(path.parent)
            temp = path.with_suffix(".tmp")
            temp.write_text(
                json.dumps({"keys": sorted(keys)}, indent=2) + "\n",
                encoding="utf-8",
            )
            replace_private_file(temp, path)
            ensure_private_file(path)
