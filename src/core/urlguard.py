"""Refusing to fetch a URL that points back inside the network.

The extractor allowlist constrains what *yt-dlp* will fetch, but image URLs are
fetched directly by us, and they arrive from a platform's JSON — or, for X
photos, from a third-party mirror. A compromised or hostile source could hand us
`http://192.168.1.1/` and we would dutifully request it from inside the home
network.

The check is deliberately simple: https only, and the host must not resolve to a
private, loopback or link-local address.
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

log = logging.getLogger(__name__)

ALLOWED_SCHEMES = frozenset({"https"})


class UnsafeUrl(Exception):
    """The URL is not somewhere we are willing to send a request."""


def _is_public(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (parsed.is_private or parsed.is_loopback or parsed.is_link_local
                or parsed.is_reserved or parsed.is_multicast
                or parsed.is_unspecified)


def resolved_addresses(host: str) -> list[str]:
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None)]
    except socket.gaierror:
        return []


def ensure_safe(url: str) -> None:
    """Raises UnsafeUrl for anything not plainly on the public internet."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeUrl(f"scheme {parsed.scheme!r} is not allowed")
    if not parsed.hostname:
        raise UnsafeUrl("no host in url")

    addresses = resolved_addresses(parsed.hostname)
    if not addresses:
        raise UnsafeUrl(f"{parsed.hostname} does not resolve")
    unsafe = [address for address in addresses if not _is_public(address)]
    if unsafe:
        log.warning("refusing %s: resolves to %s", parsed.hostname, unsafe)
        raise UnsafeUrl(f"{parsed.hostname} resolves to a non-public address")
