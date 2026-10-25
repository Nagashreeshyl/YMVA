"""`ymva` command: run migrations, serve the API, and admin maintenance."""

import argparse
import asyncio
import os
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="ymva")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="Apply database migrations")
    serve_p = sub.add_parser("serve", help="Apply migrations, then start the API server")
    serve_p.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    sub.add_parser(
        "reset-admin", help="Create or reset the admin from ADMIN_EMAIL / ADMIN_PASSWORD in .env"
    )
    args = parser.parse_args()

    if args.command == "migrate":
        migrate()
    elif args.command == "serve":
        migrate()
        serve(args.reload)
    elif args.command == "reset-admin":
        migrate()
        print(asyncio.run(_reset_admin()))
