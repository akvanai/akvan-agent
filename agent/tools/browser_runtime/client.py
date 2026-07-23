"""HTTP client and local lifecycle for the optional shared browser runtime."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from agent.config import akvan_home
from agent.tools.browser_runtime.config import (
    browser_config,
    browser_runtime_config,
    profiles_dir,
    runtime_base_url,
)
from agent.tools.browser_runtime.docker import DockerRuntimeError, ensure_docker_runtime


class BrowserRuntimeError(RuntimeError):
    pass


class BrowserRuntimeClient:
    def __init__(self, *, project_root=None, timeout: float = 90.0) -> None:
        self.project_root = project_root
        self.config = browser_runtime_config(project_root=project_root)
        self.base_url = runtime_base_url(project_root=project_root).rstrip("/")
        self.timeout = timeout
        self._recreated_stale_docker = False

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, json=payload)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            return self._send(method, url, **kwargs)
        except BrowserRuntimeError as exc:
            if self._should_recreate_stale_docker(exc, path):
                self._recreate_docker_runtime()
                try:
                    return self._send(method, url, **kwargs)
                except BrowserRuntimeError as retry_exc:
                    raise self._unavailable_error(retry_exc) from retry_exc
            if not self._should_autostart(exc):
                if self._should_start_docker(exc):
                    self._start_docker_runtime()
                    try:
                        return self._send(method, url, **kwargs)
                    except BrowserRuntimeError as retry_exc:
                        raise self._unavailable_error(retry_exc) from retry_exc
                raise
            self._start_local_runtime()
            try:
                return self._send(method, url, **kwargs)
            except BrowserRuntimeError as retry_exc:
                raise self._unavailable_error(retry_exc) from retry_exc

    def _send(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        token = str(self.config.get("token") or "")
        if token:
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("Authorization", f"Bearer {token}")
            kwargs["headers"] = headers
        try:
            response = httpx.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            raise BrowserRuntimeError(
                f"Browser runtime returned HTTP {exc.response.status_code}: {detail}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise BrowserRuntimeError(
                f"Browser runtime is unavailable at {self.base_url}."
            ) from exc
        if not isinstance(data, dict):
            raise BrowserRuntimeError("Browser runtime returned a non-object response.")
        return data

    def _should_autostart(self, exc: BrowserRuntimeError) -> bool:
        return self.config.get("mode") == "local" and "unavailable" in str(exc).lower()

    def _should_start_docker(self, exc: BrowserRuntimeError) -> bool:
        return self.config.get("mode") == "docker" and "unavailable" in str(exc).lower()

    def _should_recreate_stale_docker(self, exc: BrowserRuntimeError, path: str) -> bool:
        if self._recreated_stale_docker:
            return False
        if self.config.get("mode") != "docker":
            return False
        message = str(exc)
        # install.sh replaces ~/.akvan/app via mv; the running container can keep a
        # dead bind mount while /health still answers from the old process.
        if (
            "No such file or directory" in message
            and "/app/agent/tools/browser_runtime/" in message
        ):
            return True
        normalized = path.lstrip("/")
        if not (normalized.startswith("browser/") or normalized == "browser"):
            return False
        return "HTTP 404" in message

    def _recreate_docker_runtime(self) -> None:
        """Force-recreate a Docker runtime with a stale API or broken /app mount."""

        self._recreated_stale_docker = True
        from agent.tools.browser_runtime.docker import remove_docker_runtime

        try:
            remove_docker_runtime(
                container_name=str(
                    self.config.get("container_name") or "akvan-agent-browser-runtime"
                )
            )
            ensure_docker_runtime(config=self.config, project_root=self.project_root)
        except DockerRuntimeError as exc:
            raise BrowserRuntimeError(str(exc)) from exc
        self._wait_until_ready()

    def _start_local_runtime(self) -> None:
        log_dir = akvan_home() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "browser-runtime.log"
        root = Path(self.project_root).resolve() if self.project_root else Path.cwd().resolve()
        browser_cfg = browser_config(project_root=self.project_root)
        profiles_root = profiles_dir(project_root=self.project_root)
        profiles_root.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            "-m",
            "agent.tools.browser_runtime.server",
            "--host",
            str(self.config["host"]),
            "--port",
            str(self.config["port"]),
            "--profiles-dir",
            str(profiles_root),
            "--inactivity-timeout",
            str(browser_cfg["inactivity_timeout_seconds"]),
        ]
        with log_file.open("ab") as stream:
            subprocess.Popen(
                cmd,
                cwd=str(root),
                stdout=stream,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        self._wait_until_ready()

    def _start_docker_runtime(self) -> None:
        try:
            ensure_docker_runtime(config=self.config, project_root=self.project_root)
        except DockerRuntimeError as exc:
            raise BrowserRuntimeError(str(exc)) from exc
        self._wait_until_ready()

    def _wait_until_ready(self) -> None:
        health_url = f"{self.base_url}/health"
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            try:
                response = httpx.get(health_url, timeout=1.0)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)

    def _unavailable_error(self, exc: BrowserRuntimeError) -> BrowserRuntimeError:
        if self.config.get("mode") == "docker":
            return BrowserRuntimeError(
                f"Browser runtime is configured for Docker at {self.base_url}, but it is not reachable. "
                "Publish the runtime container port to the configured host/port, or switch browser_runtime.mode to local."
            )
        return BrowserRuntimeError(
            f"Browser runtime could not be started at {self.base_url}. "
            "Install the browser extras with `pip install akvan-agent[browser]` or run `playwright install chromium`. "
            f"Runtime log: {akvan_home() / 'logs' / 'browser-runtime.log'}"
        )
