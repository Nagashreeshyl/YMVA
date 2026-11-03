"""Guards requests YMVA makes to addresses that admins (or a caller's words) choose: HTTP, MCP
and database tools, the alert webhook and Exotel's subdomain. They must not reach this server,
its other containers or the cloud's metadata service (on AWS, 169.254.169.254 hands out the
server's credentials).

The address is checked when the connection is opened, and the connection goes to exactly the
IP that was checked, so a DNS name that changes between check and connect (DNS rebinding)
can't slip through. Private networks are blocked too unless EGRESS_ALLOW_PRIVATE is set (for
an in-house CRM on the company network).
"""

import asyncio
import ipaddress
from typing import Any
from urllib.parse import urlsplit

import httpx

from ymva.config import get_settings

# The other containers of this stack, by name.
INTERNAL_HOSTS = {"api", "web", "postgres", "redis", "caddy", "prometheus", "grafana", "backup"}


class BlockedDestinationError(httpx.RequestError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


# What EGRESS_ALLOW_PRIVATE opens: company networks only, never metadata, loopback or 0.0.0.0.
COMPANY_NETWORKS = [
    ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
]


def _address(ip: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr.ipv4_mapped
    return addr


def _blocked(ip: str) -> bool:
    addr = _address(ip)
    if get_settings().egress_allow_private and any(addr in n for n in COMPANY_NETWORKS):
        return False
    return (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_private
    )


async def safe_ip(host: str) -> str:
    """The IP to connect to for `host`, or BlockedDestinationError. Every address the name
    resolves to must be allowed, so a mixed answer can't be used to reach a blocked one."""
    host = host.strip("[]").lower()
    if not host or host in INTERNAL_HOSTS or host == "localhost" or host.endswith(".localhost"):
        raise BlockedDestinationError(f"YMVA doesn't send requests to {host or 'that address'}")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError as exc:
        raise httpx.ConnectError(f"Couldn't find {host}") from exc
    ips = [str(info[4][0]) for info in infos]
    if not ips or any(_blocked(ip) for ip in ips) or not await _outside_stack(ips):
        raise BlockedDestinationError(
            f"YMVA doesn't send requests to {host}: it's this server or an internal address"
        )
    return ips[0]


async def _outside_stack(ips: list[str]) -> bool:
    """With private networks allowed, this stack's own containers (on a private Docker network)
    stay blocked by their current addresses."""
    if not get_settings().egress_allow_private:
        return True  # private addresses are already blocked
    loop = asyncio.get_running_loop()
    found = await asyncio.gather(
        *(loop.getaddrinfo(h, None) for h in INTERNAL_HOSTS), return_exceptions=True
    )
    ours = {
        _address(str(info[4][0]))
        for result in found
        if not isinstance(result, BaseException)
        for info in result
    }
    return not any(_address(ip) in ours for ip in ips)


async def check_destination(url: str) -> None:
    await safe_ip(urlsplit(url).hostname or "")


async def _on_request(request: Any) -> None:
    # Early, readable refusal (also on every redirect); the transport enforces it on connect.
    await check_destination(str(request.url))


# httpx/httpx2 client hooks.
EVENT_HOOKS: dict[str, list[Any]] = {"request": [_on_request]}


class _GuardedBackend:
    """Network backend that connects to the IP it has just checked, never re-resolving."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def connect_tcp(self, host: str, port: int, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.connect_tcp(await safe_ip(host), port, *args, **kwargs)

    async def connect_unix_socket(self, *_: Any, **__: Any) -> Any:
        raise BlockedDestinationError("YMVA doesn't send requests to local sockets")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def _guard(transport: Any) -> Any:
    # httpx exposes no option for a network backend; its connection pool does.
    pool = transport._pool
    pool._network_backend = _GuardedBackend(pool._network_backend)
    return transport


def guarded_transport() -> httpx.AsyncHTTPTransport:
    """For httpx clients whose address comes from settings or a caller's words."""
    return _guard(httpx.AsyncHTTPTransport())  # type: ignore[no-any-return]


def guarded_transport2() -> Any:
    """The same for httpx2 (the MCP client library)."""
    import httpx2

    return _guard(httpx2.AsyncHTTPTransport())
