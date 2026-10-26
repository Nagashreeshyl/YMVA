"""Draining before a restart: no new outbound or campaign calls start, live calls finish, and
the restart waits for them (see `ymva drain` and ./ymva update)."""

from ymva.redis import get_redis

DRAIN_KEY = "ymva:draining"


async def is_draining() -> bool:
    return bool(await get_redis().exists(DRAIN_KEY))


async def start_draining(max_seconds: int) -> None:
    # Expires by itself, so a failed update never leaves calling switched off for good.
    await get_redis().set(DRAIN_KEY, "1", ex=max_seconds + 120)


async def stop_draining() -> None:
    await get_redis().delete(DRAIN_KEY)
