"""Guards requests YMVA makes to addresses that admins (or a caller's words) choose: HTTP and
MCP tools and the alert webhook. They must not reach this server itself or the cloud's
metadata service (on AWS, 169.254.169.254 hands out the server's credentials)."""

import asyncio
import ipaddress
from typing import Any
from urllib.parse import urlsplit

import httpx

# The other containers of this stack, by name.
INTERNAL_HOSTS = {"api", "web", "postgres", "redis", "caddy", "prometheus", "grafana", "backup"}


class BlockedDestinationError(httpx.RequestError):
    pass


def _blocked(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast


async def check_destination(url: str) -> None:
    host = (urlsplit(url).hostname or "").lower()
    if not host or host in INTERNAL_HOSTS or host == "localhost" or host.endswith(".localhost"):
        raise BlockedDestinationError(f"YMVA doesn't send requests to {host or 'that address'}")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError:
        return  # unknown name: the request itself fails with a normal error
    # shortcut: checked at lookup time, so DNS that changes between lookup and connect could
    # slip past; upgrade to pinning the checked IP if tools ever accept untrusted URLs.
    if any(_blocked(str(info[4][0])) for info in infos):
        raise BlockedDestinationError(
            f"YMVA doesn't send requests to {host}: it's this server or an internal address"
        )


async def _on_request(request: Any) -> None:
    await check_destination(str(request.url))


# httpx/httpx2 client hooks; they also run on every redirect.
EVENT_HOOKS: dict[str, list[Any]] = {"request": [_on_request]}
