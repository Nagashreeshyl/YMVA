from fastapi import APIRouter, Response, status
from sqlalchemy import text

from ymva import __version__
from ymva.db import get_engine
from ymva.logging import get_logger
from ymva.redis import get_redis

router = APIRouter(prefix="/health", tags=["health"])
log = get_logger(__name__)


@router.get("")
async def liveness() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/ready")
async def readiness(response: Response) -> dict[str, object]:
    checks: dict[str, str] = {}
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception as exc:
        log.error("readiness: database check failed", error=str(exc))
        checks["database"] = "error"
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        log.error("readiness: redis check failed", error=str(exc))
        checks["redis"] = "error"
    ready = all(v == "ok" for v in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ready else "degraded", "checks": checks}
