"""URL safety / SSRF guard for outbound fetches and browsing (spec §8).

Blocks non-http(s) schemes, embedded credentials and — unless explicitly
allowed — any host that resolves to a private, loopback, link-local or
otherwise non-public address (cloud metadata lives at 169.254.169.254).
DNS failures are left to the fetcher so unreachable hosts still fail with
their honest NETWORK_ERROR.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

BLOCKED_CODES = {
    "INVALID_URL",
    "BLOCKED_SCHEME",
    "BLOCKED_CREDENTIALS",
    "BLOCKED_PRIVATE",
}


def _is_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_external_url(url: str, *, allow_private: bool = False) -> str | None:
    """Return a blocking error code if the URL must not be fetched, else None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "INVALID_URL"
    if not parsed.scheme:
        return "INVALID_URL"
    if parsed.scheme not in ("http", "https"):
        return "BLOCKED_SCHEME"
    if not parsed.hostname:
        return "INVALID_URL"
    if parsed.username or parsed.password:
        return "BLOCKED_CREDENTIALS"
    if allow_private:
        return None

    host = parsed.hostname
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        return "BLOCKED_PRIVATE" if _is_private(literal) else None
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return None  # unresolvable — let the fetch produce its honest error
    for info in infos:
        addr = info[4][0]
        try:
            if _is_private(ipaddress.ip_address(addr)):
                return "BLOCKED_PRIVATE"
        except ValueError:
            continue
    return None


def blocked_message(code: str, url: str) -> str:
    return {
        "BLOCKED_SCHEME": f"Only http/https URLs may be fetched: {url}",
        "BLOCKED_CREDENTIALS": f"URLs with embedded credentials are blocked: {url}",
        "BLOCKED_PRIVATE": (
            f"Refusing to fetch a private/internal address: {url}. "
            "Set JALORA_BROWSE_ALLOW_PRIVATE=true to allow local browsing."
        ),
    }.get(code, f"URL blocked: {url}")
