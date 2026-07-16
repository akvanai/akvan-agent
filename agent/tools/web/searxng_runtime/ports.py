"""Port availability helpers for managed SearXNG."""

from __future__ import annotations

import socket


def is_port_free(host: str, port: int) -> bool:
    if port < 1 or port > 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def suggest_next_port(host: str, start: int, *, max_tries: int = 100) -> int:
    port = max(1, start)
    for _ in range(max_tries):
        if is_port_free(host, port):
            return port
        port += 1
    raise RuntimeError(f"No free port found near {start} on {host}.")
