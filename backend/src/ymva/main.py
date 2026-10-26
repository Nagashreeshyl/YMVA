"""FastAPI application factory."""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ymva import __version__
from ymva.api.routes import (
    agents,
    audit_logs,
    auth,
    builder,
    calls,
    campaigns,
    health,
    integrations,
    numbers,
    overview,
    telephony,
    tools,
    users,
)
from ymva.config import get_settings
from ymva.db import get_engine, get_sessionmaker
from ymva.logging import configure_logging, get_logger
from ymva.redis import get_redis
from ymva.services import dialer
from ymva.services.bootstrap import ensure_admin

log = get_logger(__name__)

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    async with get_sessionmaker()() as session:
        await ensure_admin(session)
    log.info("ymva api started", version=__version__, environment=get_settings().environment)
    dialer_task = (
        asyncio.create_task(dialer.run_forever()) if get_settings().dialer_enabled else None
    )
    yield
    if dialer_task:
        dialer_task.cancel()
    await get_redis().aclose()
    await get_engine().dispose()


def _origin_allowed(request: Request) -> bool:
    """CSRF guard: browser requests that change state must come from our own origin."""
    origin = request.headers.get("origin")
    if origin is None or request.headers.get("authorization"):
        return True
    settings = get_settings()
    allowed = {settings.public_url, *settings.cors_origins}
    allowed_hosts = {urlsplit(o).netloc for o in allowed}
    return urlsplit(origin).netloc in allowed_hosts or urlsplit(origin).netloc == request.url.netloc


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="YMVA API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.environment != "production" else None,
        openapi_url="/api/openapi.json" if settings.environment != "production" else None,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        if request.method in UNSAFE_METHODS and not _origin_allowed(request):
            return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", path=request.url.path, method=request.method)
            response = JSONResponse(
                {"detail": "Something went wrong. It has been logged.", "request_id": request_id},
                status_code=500,
            )
        response.headers["x-request-id"] = request_id
        if not request.url.path.startswith("/api/health"):
            log.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                ms=round((time.perf_counter() - started) * 1000, 1),
            )
        return response

    api = APIRouter(prefix="/api")
    for module in (
        health,
        auth,
        users,
        audit_logs,
        overview,
        integrations,
        numbers,
        agents,
        builder,
        calls,
        campaigns,
        tools,
        telephony,
    ):
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
