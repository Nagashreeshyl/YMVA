"""`ymva` command: run migrations, serve the API, and admin maintenance."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config


def _backend_dir() -> Path:
    for candidate in (Path.cwd(), Path(__file__).resolve().parents[2]):
        if (candidate / "alembic.ini").is_file():
            return candidate
    raise SystemExit("alembic.ini not found; run ymva from the backend directory")


def migrate() -> None:
    backend_dir = _backend_dir()
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")


def serve(reload: bool) -> None:
    import uvicorn

    uvicorn.run(
        "ymva.main:app",
        # Local-only by default; the container sets HOST=0.0.0.0.
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=reload,
        # One process tops out near 10 calls (Python uses one core); more workers, more calls.
        workers=None if reload else int(os.environ.get("API_WORKERS", "1")),
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        timeout_graceful_shutdown=30,
        log_config=None,
    )


async def _reset_admin() -> str:
    from ymva.config import get_settings
    from ymva.db import get_engine, get_sessionmaker
    from ymva.redis import get_redis
    from ymva.services.bootstrap import reset_admin

    async with get_sessionmaker()() as session:
        message = await reset_admin(session)
    # Clear any sign-in lockout for this email.
    redis = get_redis()
    email = get_settings().admin_email.strip().lower()
    async for key in redis.scan_iter(match=f"ratelimit:login:{email}:*"):
        await redis.delete(key)
    await redis.delete(f"ratelimit:login-account:{email}")
    await redis.aclose()
    await get_engine().dispose()
    return message


async def _drain(wait_seconds: int) -> int:
    """Stop new calls and wait for live ones to finish. Returns how many are still live."""
    import time

    from sqlalchemy import func, select

    from ymva.db import get_engine, get_sessionmaker
    from ymva.models import Call
    from ymva.redis import get_redis
    from ymva.services.calls import ACTIVE_STATUSES
    from ymva.services.drain import start_draining

    await start_draining(wait_seconds)
    deadline, live, said = time.monotonic() + wait_seconds, 0, -1
    while True:
        async with get_sessionmaker()() as session:
            live = (
                await session.scalar(
                    select(func.count()).select_from(Call).where(Call.status.in_(ACTIVE_STATUSES))
                )
                or 0
            )
        if live != said:
            print(f"Live calls: {live}", flush=True)
            said = live
        if live == 0 or time.monotonic() > deadline:
            break
        await asyncio.sleep(5)
    await get_redis().aclose()
    await get_engine().dispose()
    return live


def main() -> None:
    parser = argparse.ArgumentParser(prog="ymva")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="Apply database migrations")
    serve_p = sub.add_parser("serve", help="Apply migrations, then start the API server")
    serve_p.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    sub.add_parser(
        "reset-admin", help="Create or reset the admin from ADMIN_EMAIL / ADMIN_PASSWORD in .env"
    )
    drain_p = sub.add_parser(
        "drain", help="Stop starting new calls and wait for live calls to end (before a restart)"
    )
    drain_p.add_argument("--wait", type=int, default=900, help="Longest wait, in seconds")
    sim_p = sub.add_parser(
        "simulate", help="Spoken test calls through a simulated phone network (regression/load)"
    )
    sim_p.add_argument("--calls", type=int, default=4, help="Simultaneous calls (default 4)")
    sim_p.add_argument("--agent", help="Agent name (default: the oldest published agent)")
    sim_p.add_argument("--keep", action="store_true", help="Keep the test calls afterwards")
    args = parser.parse_args()

    if args.command == "migrate":
        migrate()
    elif args.command == "serve":
        migrate()
        serve(args.reload)
    elif args.command == "drain":
        left = asyncio.run(_drain(args.wait))
        if left:
            print(f"Stopped waiting with {left} call(s) still live.")
    elif args.command == "simulate":
        from ymva.simulate import run  # imports audio libraries only when simulating

        sys.exit(asyncio.run(run(args.agent, args.calls, args.keep)))
    elif args.command == "reset-admin":
        migrate()
        print(asyncio.run(_reset_admin()))
