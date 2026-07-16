"""``akvan logs`` — view and filter Akvan log files."""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from agent.config import akvan_home
from agent.logging_setup import component_prefixes, logs_dir

LOG_FILES = {
    "agent": "agent.log",
    "errors": "errors.log",
}

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})")
_LEVEL_RE = re.compile(r"\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s")
_LOGGER_NAME_RE = re.compile(
    r"\s(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)"
    r"(?:\s+\[.*?\])?"
    r"\s+(\S+):"
)
_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


def build_logs_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="View Akvan log files.")
    parser.add_argument(
        "log_name",
        nargs="?",
        default="agent",
        help="Log to read: agent, errors, gateway, or list.",
    )
    parser.add_argument(
        "gateway_id",
        nargs="?",
        default=None,
        help="Gateway id when log_name is gateway (e.g. telegram).",
    )
    parser.add_argument(
        "-n",
        "--lines",
        type=int,
        default=50,
        help="Number of recent lines to show (default: 50).",
    )
    parser.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow log output (Ctrl+C to stop).",
    )
    parser.add_argument(
        "--level",
        default=None,
        help="Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="Filter by session id substring.",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Show lines from the last duration (e.g. 1h, 30m, 2d).",
    )
    parser.add_argument(
        "--component",
        default=None,
        help="Filter by component: memory, skills, review, session, gateway, agent, tools.",
    )
    return parser


def resolve_log_path(log_name: str, gateway_id: str | None = None) -> Path | None:
    if log_name == "list":
        return None
    if log_name == "gateway":
        gw = gateway_id or "telegram"
        return logs_dir() / f"gateway-{gw}.log"
    filename = LOG_FILES.get(log_name)
    if filename is None:
        return None
    return logs_dir() / filename


def _parse_since(since_str: str) -> datetime | None:
    since_str = since_str.strip().lower()
    match = re.match(r"^(\d+)\s*([smhd])$", since_str)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    delta = {
        "s": timedelta(seconds=value),
        "m": timedelta(minutes=value),
        "h": timedelta(hours=value),
        "d": timedelta(days=value),
    }[unit]
    return datetime.now() - delta


def _parse_line_timestamp(line: str) -> datetime | None:
    match = _TS_RE.match(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _extract_level(line: str) -> str | None:
    match = _LEVEL_RE.search(line)
    return match.group(1) if match else None


def _extract_logger_name(line: str) -> str | None:
    match = _LOGGER_NAME_RE.search(line)
    return match.group(1) if match else None


def _line_matches_component(line: str, prefixes: tuple[str, ...]) -> bool:
    name = _extract_logger_name(line)
    if name is None:
        return False
    return name.startswith(prefixes)


def _matches_filters(
    line: str,
    *,
    min_level: str | None = None,
    session_filter: str | None = None,
    since: datetime | None = None,
    component_prefixes: tuple[str, ...] | None = None,
) -> bool:
    if since is not None:
        ts = _parse_line_timestamp(line)
        if ts is not None and ts < since:
            return False

    if min_level is not None:
        level = _extract_level(line)
        if level is not None:
            if _LEVEL_ORDER.get(level, 0) < _LEVEL_ORDER.get(min_level, 0):
                return False

    if session_filter is not None and session_filter not in line:
        return False

    if component_prefixes is not None:
        if not _line_matches_component(line, component_prefixes):
            return False

    return True


def _read_last_n_lines(path: Path, n: int) -> list[str]:
    try:
        size = path.stat().st_size
        if size == 0:
            return []

        if size <= 1_048_576:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                return handle.readlines()[-n:]

        with path.open("rb") as handle:
            chunk_size = 8192
            lines: list[bytes] = []
            pos = size
            while pos > 0 and len(lines) <= n + 1:
                read_size = min(chunk_size, pos)
                pos -= read_size
                handle.seek(pos)
                chunk = handle.read(read_size)
                chunk_lines = chunk.split(b"\n")
                if lines:
                    lines[0] = chunk_lines[-1] + lines[0]
                    lines = chunk_lines[:-1] + lines
                else:
                    lines = chunk_lines
                chunk_size = min(chunk_size * 2, 65536)

            decoded: list[str] = []
            for raw in lines:
                if not raw.strip():
                    continue
                decoded.append(raw.decode("utf-8", errors="replace") + "\n")
            return decoded[-n:]
    except OSError:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.readlines()[-n:]


def _read_tail(
    path: Path,
    num_lines: int,
    *,
    has_filters: bool = False,
    min_level: str | None = None,
    session_filter: str | None = None,
    since: datetime | None = None,
    component_prefixes: tuple[str, ...] | None = None,
) -> list[str]:
    if has_filters:
        raw_lines = _read_last_n_lines(path, max(num_lines * 20, 2000))
        filtered = [
            line
            for line in raw_lines
            if _matches_filters(
                line,
                min_level=min_level,
                session_filter=session_filter,
                since=since,
                component_prefixes=component_prefixes,
            )
        ]
        return filtered[-num_lines:]
    return _read_last_n_lines(path, num_lines)


def _follow_log(
    path: Path,
    *,
    min_level: str | None = None,
    session_filter: str | None = None,
    since: datetime | None = None,
    component_prefixes: tuple[str, ...] | None = None,
) -> None:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, 2)
        while True:
            line = handle.readline()
            if line:
                if _matches_filters(
                    line,
                    min_level=min_level,
                    session_filter=session_filter,
                    since=since,
                    component_prefixes=component_prefixes,
                ):
                    print(line, end="")
                    sys.stdout.flush()
            else:
                time.sleep(0.3)


def list_logs() -> int:
    log_dir = logs_dir()
    if not log_dir.exists():
        print(f"No logs directory at {log_dir}")
        return 0

    print(f"Log files in {log_dir}:\n")
    found = False
    for entry in sorted(log_dir.iterdir()):
        if entry.is_file() and entry.suffix == ".log":
            size = entry.stat().st_size
            if size < 1024:
                size_str = f"{size}B"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f}KB"
            else:
                size_str = f"{size / (1024 * 1024):.1f}MB"
            mtime = datetime.fromtimestamp(entry.stat().st_mtime)
            age = datetime.now() - mtime
            if age.total_seconds() < 3600:
                age_str = f"{int(age.total_seconds() / 60)}m ago"
            elif age.total_seconds() < 86400:
                age_str = f"{int(age.total_seconds() / 3600)}h ago"
            else:
                age_str = mtime.strftime("%Y-%m-%d")
            print(f"  {entry.name:<30} {size_str:>8}   {age_str}")
            found = True

    if not found:
        print("  (no log files yet — run `akvan` to generate logs)")
    return 0


def run_logs_with_args(args: argparse.Namespace) -> int:
    if args.log_name == "list":
        return list_logs()

    log_path = resolve_log_path(args.log_name, args.gateway_id)
    if log_path is None:
        available = ", ".join(sorted({*LOG_FILES, "gateway", "list"}))
        print(f"Unknown log: {args.log_name!r}. Available: {available}")
        return 1

    if not log_path.exists():
        print(f"Log file not found: {log_path}")
        print("(Logs are created when Akvan runs — try `akvan` first)")
        return 1

    since_dt = None
    if args.since:
        since_dt = _parse_since(args.since)
        if since_dt is None:
            print(f"Invalid --since value: {args.since!r}. Use format like '1h', '30m', '2d'.")
            return 1

    min_level = args.level.upper() if args.level else None
    if min_level and min_level not in _LEVEL_ORDER:
        print("Invalid --level. Use DEBUG, INFO, WARNING, ERROR, or CRITICAL.")
        return 1

    comp_prefixes = None
    if args.component:
        comp_prefixes = component_prefixes(args.component)
        if not comp_prefixes:
            print(
                "Unknown component. Available: memory, skills, review, "
                "session, gateway, agent, tools."
            )
            return 1

    has_filters = any(
        (min_level, args.session, since_dt, comp_prefixes)
    )

    try:
        lines = _read_tail(
            log_path,
            args.lines,
            has_filters=has_filters,
            min_level=min_level,
            session_filter=args.session,
            since=since_dt,
            component_prefixes=comp_prefixes,
        )
    except PermissionError:
        print(f"Permission denied: {log_path}")
        return 1

    filter_parts = []
    if min_level:
        filter_parts.append(f"level>={min_level}")
    if args.session:
        filter_parts.append(f"session={args.session}")
    if args.component:
        filter_parts.append(f"component={args.component}")
    if args.since:
        filter_parts.append(f"since={args.since}")
    filter_desc = f" [{', '.join(filter_parts)}]" if filter_parts else ""

    home = akvan_home()
    rel = log_path.relative_to(home) if log_path.is_relative_to(home) else log_path
    if args.follow:
        print(f"--- {home}/{rel}{filter_desc} (Ctrl+C to stop) ---")
    else:
        print(f"--- {home}/{rel}{filter_desc} (last {args.lines}) ---")

    for line in lines:
        print(line, end="")

    if not args.follow:
        return 0

    try:
        _follow_log(
            log_path,
            min_level=min_level,
            session_filter=args.session,
            since=since_dt,
            component_prefixes=comp_prefixes,
        )
    except KeyboardInterrupt:
        print("\n--- stopped ---")
    return 0
