from redis.asyncio import Redis


async def hit(redis: Redis, key: str, limit: int, window_seconds: int) -> bool:
    """Count one attempt; return False once `limit` attempts happened inside the window."""
    full_key = f"ratelimit:{key}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(full_key)
        pipe.expire(full_key, window_seconds, nx=True)
        count, _ = await pipe.execute()
    return int(count) <= limit


async def reset(redis: Redis, key: str) -> None:
    await redis.delete(f"ratelimit:{key}")
