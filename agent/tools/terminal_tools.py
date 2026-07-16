"""Terminal execution and owned background-process tools."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

from agent.tools.approval import classify_terminal
from agent.tools.base import Tool
from agent.tools.presentation import ToolPresentation, detail_from_arg
from agent.tools.process_manager import MAX_PROCESS_OUTPUT, ProcessManager


def build_terminal_tools(
    project_root: Path,
    process_manager: ProcessManager,
    *,
    default_timeout: int = 120,
) -> tuple[Tool, Tool]:
    root = project_root.resolve()

    def resolve_workdir(value: str | None) -> Path:
        path = Path(value).expanduser() if value else root
        if not path.is_absolute():
            path = root / path
        resolved = path.resolve()
        if not resolved.is_dir():
            raise ValueError(f"Working directory does not exist: {resolved}")
        return resolved

    def terminal(
        command: str,
        workdir: str | None = None,
        timeout: int | None = None,
        background: bool = False,
        pty: bool = False,
    ) -> str:
        if not command.strip():
            raise ValueError("command must not be empty")
        cwd = resolve_workdir(workdir)
        effective_timeout = default_timeout if timeout is None else timeout
        if effective_timeout < 1 or effective_timeout > 600:
            raise ValueError("timeout must be between 1 and 600 seconds")
        if background or pty:
            managed = process_manager.spawn(
                command, workdir=cwd, pty_mode=pty
            )
            if background:
                return json.dumps(managed.snapshot(), ensure_ascii=False)
            result = process_manager.wait(
                managed.session_id, timeout=effective_timeout
            )
            if result["running"]:
                process_manager.kill(managed.session_id)
                result = managed.snapshot()
                result["timed_out"] = True
            process_manager.close(managed.session_id)
            return json.dumps(result, ensure_ascii=False)
        try:
            completed = subprocess.run(
                ["/bin/bash", "-lc", command],
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=effective_timeout,
                check=False,
            )
            output = completed.stdout[-MAX_PROCESS_OUTPUT:].decode(
                "utf-8", errors="replace"
            )
            return json.dumps(
                {
                    "command": command,
                    "workdir": str(cwd),
                    "exit_code": completed.returncode,
                    "output": output,
                    "truncated": len(completed.stdout) > MAX_PROCESS_OUTPUT,
                },
                ensure_ascii=False,
            )
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or b"")[-MAX_PROCESS_OUTPUT:].decode(
                "utf-8", errors="replace"
            )
            return json.dumps(
                {
                    "command": command,
                    "workdir": str(cwd),
                    "timed_out": True,
                    "output": output,
                },
                ensure_ascii=False,
            )

    def terminal_approval(arguments: Mapping[str, object]):
        command = arguments.get("command")
        if not isinstance(command, str):
            return None
        raw_workdir = arguments.get("workdir")
        return classify_terminal(
            command,
            workdir=resolve_workdir(
                raw_workdir if isinstance(raw_workdir, str) else None
            ),
            project_root=root,
        )

    def process(
        action: str,
        session_id: str | None = None,
        timeout: float | None = None,
        data: str | None = None,
    ) -> str:
        if action == "list":
            return json.dumps(process_manager.list(), ensure_ascii=False)
        if not session_id:
            raise ValueError("session_id is required for this action")
        if action == "poll":
            result = process_manager.get(session_id).snapshot(incremental=True)
        elif action == "wait":
            result = process_manager.wait(session_id, timeout)
        elif action == "write":
            if data is None:
                raise ValueError("data is required for write")
            result = process_manager.write(session_id, data)
        elif action == "kill":
            result = process_manager.kill(session_id)
        elif action == "close":
            result = process_manager.close(session_id)
        else:
            raise ValueError(f"Unsupported process action: {action}")
        return json.dumps(result, ensure_ascii=False)

    return (
        Tool(
            "terminal",
            "Execute a local shell command in the foreground or background.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "workdir": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 600},
                    "background": {"type": "boolean", "default": False},
                    "pty": {"type": "boolean", "default": False},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            terminal,
            terminal_approval,
            presentation=ToolPresentation(
                emoji="⚡",
                label="Running command",
                style="bold #ffd166",
                format_detail=lambda args: detail_from_arg(args, "command"),
            ),
        ),
        Tool(
            "process",
            "Manage background processes created by this Akvan session.",
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "poll", "wait", "write", "kill", "close"],
                    },
                    "session_id": {"type": "string"},
                    "timeout": {"type": "number", "minimum": 0},
                    "data": {"type": "string"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            process,
            presentation=ToolPresentation(
                emoji="🔄",
                label="Managing process",
                style="bold #c9a0ff",
                format_detail=lambda args: detail_from_arg(args, "action"),
            ),
        ),
    )
