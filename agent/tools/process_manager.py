"""Local subprocess lifecycle management for terminal tools."""

from __future__ import annotations

import atexit
import os
import pty
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

MAX_PROCESS_OUTPUT = 100_000


@dataclass
class ManagedProcess:
    session_id: str
    process: subprocess.Popen[bytes]
    command: str
    workdir: Path
    master_fd: int | None = None
    output: bytearray = field(default_factory=bytearray)
    cursor: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    reader_done: threading.Event = field(default_factory=threading.Event)

    def append(self, chunk: bytes) -> None:
        with self.lock:
            self.output.extend(chunk)
            if len(self.output) > MAX_PROCESS_OUTPUT:
                removed = len(self.output) - MAX_PROCESS_OUTPUT
                del self.output[:removed]
                self.cursor = max(0, self.cursor - removed)

    def snapshot(self, *, incremental: bool = False) -> dict[str, object]:
        with self.lock:
            start = self.cursor if incremental else 0
            data = bytes(self.output[start:])
            if incremental:
                self.cursor = len(self.output)
        code = self.process.poll()
        return {
            "session_id": self.session_id,
            "command": self.command,
            "workdir": str(self.workdir),
            "running": code is None,
            "exit_code": code,
            "output": data.decode("utf-8", errors="replace"),
        }


class ProcessManager:
    """Owns only processes spawned by one Akvan session."""

    def __init__(self) -> None:
        self._processes: dict[str, ManagedProcess] = {}
        self._lock = threading.Lock()
        atexit.register(self.cleanup)

    def spawn(
        self, command: str, *, workdir: Path, pty_mode: bool = False
    ) -> ManagedProcess:
        session_id = uuid.uuid4().hex[:12]
        master_fd: int | None = None
        if pty_mode:
            master_fd, slave_fd = pty.openpty()
            try:
                process = subprocess.Popen(
                    ["/bin/bash", "-lc", command],
                    cwd=workdir,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    start_new_session=True,
                )
            finally:
                os.close(slave_fd)
        else:
            process = subprocess.Popen(
                ["/bin/bash", "-lc", command],
                cwd=workdir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        managed = ManagedProcess(
            session_id, process, command, workdir, master_fd
        )
        with self._lock:
            self._processes[session_id] = managed
        threading.Thread(
            target=self._read_output, args=(managed,), daemon=True
        ).start()
        return managed

    def get(self, session_id: str) -> ManagedProcess:
        with self._lock:
            managed = self._processes.get(session_id)
        if managed is None:
            raise ValueError(f"Unknown process session {session_id!r}.")
        return managed

    def list(self) -> list[dict[str, object]]:
        with self._lock:
            values = list(self._processes.values())
        return [
            {
                "session_id": item.session_id,
                "command": item.command,
                "running": item.process.poll() is None,
                "exit_code": item.process.poll(),
            }
            for item in values
        ]

    def wait(
        self, session_id: str, timeout: float | None = None
    ) -> dict[str, object]:
        managed = self.get(session_id)
        try:
            managed.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return managed.snapshot(incremental=True)
        managed.reader_done.wait(timeout=0.5)
        return managed.snapshot(incremental=True)

    def write(self, session_id: str, data: str) -> dict[str, object]:
        managed = self.get(session_id)
        if managed.process.poll() is not None:
            raise ValueError("Cannot write to a completed process.")
        payload = data.encode("utf-8")
        if managed.master_fd is not None:
            os.write(managed.master_fd, payload)
        elif managed.process.stdin is not None:
            managed.process.stdin.write(payload)
            managed.process.stdin.flush()
        return managed.snapshot(incremental=True)

    def kill(self, session_id: str) -> dict[str, object]:
        managed = self.get(session_id)
        if managed.process.poll() is None:
            try:
                os.killpg(managed.process.pid, 15)
                managed.process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if managed.process.poll() is None:
                    try:
                        os.killpg(managed.process.pid, 9)
                    except ProcessLookupError:
                        pass
                    managed.process.wait(timeout=2)
        managed.reader_done.wait(timeout=0.5)
        return managed.snapshot(incremental=True)

    def close(self, session_id: str) -> dict[str, object]:
        managed = self.get(session_id)
        if managed.process.poll() is None:
            raise ValueError("Cannot close a running process; kill it first.")
        result = managed.snapshot(incremental=True)
        with self._lock:
            self._processes.pop(session_id, None)
        if managed.master_fd is not None:
            try:
                os.close(managed.master_fd)
            except OSError:
                pass
        return result

    def cleanup(self) -> None:
        with self._lock:
            session_ids = list(self._processes)
        for session_id in session_ids:
            try:
                managed = self.get(session_id)
                if managed.process.poll() is None:
                    self.kill(session_id)
                self.close(session_id)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass

    @staticmethod
    def _read_output(managed: ManagedProcess) -> None:
        try:
            if managed.master_fd is not None:
                while True:
                    try:
                        chunk = os.read(managed.master_fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    managed.append(chunk)
            elif managed.process.stdout is not None:
                while True:
                    chunk = managed.process.stdout.read(4096)
                    if not chunk:
                        break
                    managed.append(chunk)
        finally:
            if managed.process.poll() is None:
                try:
                    managed.process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            managed.reader_done.set()
