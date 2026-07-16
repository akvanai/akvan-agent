"""Centralized logging setup for Akvan Agent."""

from __future__ import annotations

import logging
import re
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from agent.config import akvan_home
from agent.logging_config import LoggingConfig, load_logging_config
from agent.storage.permissions import (
    ensure_private_dir,
    ensure_private_file,
    is_under_akvan_home,
)

_LOG_FORMAT = "%(asctime)s %(levelname)s%(session_tag)s %(name)s: %(message)s"
_LOG_FORMAT_VERBOSE = "%(asctime)s - %(name)s - %(levelname)s%(session_tag)s - %(message)s"

_NOISY_LOGGERS = (
    "openai",
    "openai._base_client",
    "httpx",
    "httpcore",
    "asyncio",
    "urllib3",
    "urllib3.connectionpool",
    "websockets",
)

_COMPONENT_PREFIXES = {
    "memory": ("akvan.memory",),
    "skills": ("akvan.skills",),
    "review": ("akvan.review",),
    "session": ("akvan.session",),
    "gateway": ("akvan.gateway", "agent.gateway"),
    "agent": ("agent", "akvan"),
    "tools": ("agent.tools",),
}

_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "sk-***"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-+/=]{8,}\b", re.I), "Bearer ***"),
    (re.compile(r"\b(api[_-]?key|token|secret)\s*[:=]\s*\S+", re.I), r"\1=***"),
]

_logging_initialized = False
_session_context = threading.local()


def logs_dir(*, project_root: Path | None = None) -> Path:
    return (project_root or akvan_home()) / "logs"


def set_session_context(session_id: str) -> None:
    """Set the session ID for the current thread."""
    _session_context.session_id = session_id


def clear_session_context() -> None:
    """Clear the session ID for the current thread."""
    _session_context.session_id = None


def _install_session_record_factory() -> None:
    current_factory = logging.getLogRecordFactory()
    if getattr(current_factory, "_akvan_session_injector", False):
        return

    def _session_record_factory(*args, **kwargs):
        record = current_factory(*args, **kwargs)
        sid = getattr(_session_context, "session_id", None)
        record.session_tag = f" [{sid[:8]}]" if sid else ""  # type: ignore[attr-defined]
        return record

    _session_record_factory._akvan_session_injector = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(_session_record_factory)


_install_session_record_factory()


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts common secret patterns from log messages."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        for pattern, replacement in _REDACT_PATTERNS:
            formatted = pattern.sub(replacement, formatted)
        return formatted


class _ComponentFilter(logging.Filter):
    def __init__(self, prefixes: tuple[str, ...]) -> None:
        super().__init__()
        self._prefixes = prefixes

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(self._prefixes)


def setup_logging(
    *,
    project_root: Path | None = None,
    mode: str | None = None,
    gateway_id: str | None = None,
    force: bool = False,
) -> Path:
    """Configure Akvan logging. Safe to call multiple times (idempotent)."""
    global _logging_initialized

    root = akvan_home() if project_root is None else project_root
    log_dir = logs_dir(project_root=root)
    if is_under_akvan_home(root):
        ensure_private_dir(log_dir)
    else:
        log_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_logging_config(project_root=root)
    level = getattr(logging, cfg.level, logging.INFO)
    max_bytes = cfg.max_size_mb * 1024 * 1024
    backups = cfg.backup_count

    if mode == "gateway":
        gw_id = gateway_id or "gateway"
        main_log = log_dir / f"gateway-{gw_id}.log"
    else:
        main_log = log_dir / "agent.log"

    _add_rotating_handler(
        logging.getLogger(),
        main_log,
        level=level,
        max_bytes=max_bytes,
        backup_count=backups,
        formatter=RedactingFormatter(_LOG_FORMAT),
    )
    _add_rotating_handler(
        logging.getLogger(),
        log_dir / "errors.log",
        level=logging.WARNING,
        max_bytes=2 * 1024 * 1024,
        backup_count=2,
        formatter=RedactingFormatter(_LOG_FORMAT),
    )

    if cfg.console:
        _add_console_handler(logging.getLogger(), level=level)

    if _logging_initialized and not force:
        return log_dir

    root_logger = logging.getLogger()
    if root_logger.level == logging.NOTSET or root_logger.level > level:
        root_logger.setLevel(level)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _logging_initialized = True
    return log_dir


def setup_verbose_logging() -> None:
    """Enable DEBUG-level console logging for verbose mode."""
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, RotatingFileHandler
        ):
            if getattr(handler, "_akvan_verbose", False):
                return

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(RedactingFormatter(_LOG_FORMAT_VERBOSE, datefmt="%H:%M:%S"))
    handler._akvan_verbose = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    if root.level > logging.DEBUG:
        root.setLevel(logging.DEBUG)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def truncate_summary(text: str, *, limit: int = 120) -> str:
    """Truncate a log summary to keep log files small."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _add_console_handler(logger: logging.Logger, *, level: int) -> None:
    for existing in logger.handlers:
        if isinstance(existing, logging.StreamHandler) and not isinstance(
            existing, RotatingFileHandler
        ):
            if getattr(existing, "_akvan_console", False):
                return

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(RedactingFormatter(_LOG_FORMAT))
    handler._akvan_console = True  # type: ignore[attr-defined]
    logger.addHandler(handler)


def _add_rotating_handler(
    logger: logging.Logger,
    path: Path,
    *,
    level: int,
    max_bytes: int,
    backup_count: int,
    formatter: logging.Formatter,
    log_filter: Optional[logging.Filter] = None,
) -> None:
    resolved = path.resolve()
    for existing in logger.handlers:
        if (
            isinstance(existing, RotatingFileHandler)
            and Path(getattr(existing, "baseFilename", "")).resolve() == resolved
        ):
            return

    if is_under_akvan_home(path):
        ensure_private_dir(path.parent)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    if log_filter is not None:
        handler.addFilter(log_filter)
    logger.addHandler(handler)
    if is_under_akvan_home(path):
        ensure_private_file(path)


def component_prefixes(component: str) -> tuple[str, ...]:
    """Return logger name prefixes for a component filter."""
    return _COMPONENT_PREFIXES.get(component.lower(), ())
