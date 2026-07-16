"""SSRF protection for web_extract URL targets."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from urllib.parse import quote, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

_BLOCKED_HOSTNAMES = frozenset({"metadata.google.internal", "metadata.goog"})
_ALWAYS_BLOCKED_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.170.2"),
        ipaddress.ip_address("169.254.169.253"),
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("::ffff:169.254.169.254"),
        ipaddress.ip_address("::ffff:169.254.170.2"),
        ipaddress.ip_address("::ffff:169.254.169.253"),
        ipaddress.ip_address("::ffff:100.100.100.200"),
    }
)
_ALWAYS_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::ffff:169.254.0.0/112"),
)
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def normalize_url_for_request(url: str) -> str:
    if not isinstance(url, str):
        return url
    raw = url.strip()
    if not raw:
        return raw
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    if parsed.scheme.lower() not in {"http", "https"}:
        return raw
    netloc = parsed.netloc
    hostname = parsed.hostname
    if hostname:
        try:
            ascii_host = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            ascii_host = hostname
        if ascii_host != hostname:
            netloc = netloc.replace(hostname, ascii_host, 1)
    path = quote(parsed.path, safe="/%:@!$&'()*+,;=")
    query = quote(parsed.query, safe="/%:@!$&'()*+,;=?")
    fragment = quote(parsed.fragment, safe="/%:@!$&'()*+,;=?")
    return urlunsplit((parsed.scheme, netloc, path, query, fragment))


def _allow_private_urls() -> bool:
    value = os.getenv("AKVAN_ALLOW_PRIVATE_URLS", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        embedded = ip.ipv4_mapped
        return (
            embedded.is_private
            or embedded.is_loopback
            or embedded.is_link_local
            or embedded.is_reserved
            or embedded.is_multicast
            or embedded.is_unspecified
            or embedded in _CGNAT_NETWORK
        )
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return True
    if ip.is_multicast or ip.is_unspecified:
        return True
    if ip in _CGNAT_NETWORK:
        return True
    return False


def is_safe_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        scheme = (parsed.scheme or "").strip().lower()
        if scheme not in {"http", "https"}:
            logger.warning("Blocked request — unsupported URL scheme: %s", scheme or "<empty>")
            return False
        if not hostname:
            return False
        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning("Blocked request to internal hostname: %s", hostname)
            return False
        allow_private = _allow_private_urls()
        try:
            addr_info = socket.getaddrinfo(
                hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
        except socket.gaierror:
            logger.warning("Blocked request — DNS resolution failed for: %s", hostname)
            return False
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            if "%" in ip_str:
                ip_str = ip_str.split("%")[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                logger.warning(
                    "Blocked request — unparseable IP address %r for hostname %s",
                    sockaddr[0],
                    hostname,
                )
                return False
            if ip in _ALWAYS_BLOCKED_IPS or any(
                ip in net for net in _ALWAYS_BLOCKED_NETWORKS
            ):
                logger.warning(
                    "Blocked request to cloud metadata address: %s -> %s",
                    hostname,
                    ip_str,
                )
                return False
            if not allow_private and _is_blocked_ip(ip):
                logger.warning(
                    "Blocked request to private/internal address: %s -> %s",
                    hostname,
                    ip_str,
                )
                return False
        return True
    except Exception as exc:
        logger.warning("Blocked request — URL safety check error for %s: %s", url, exc)
        return False


async def async_is_safe_url(url: str) -> bool:
    return await asyncio.to_thread(is_safe_url, url)
