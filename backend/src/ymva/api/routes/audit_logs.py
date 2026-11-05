import csv
import io
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from ymva.api.deps import AuditViewer, DbSession, client_ip
from ymva.api.routes.campaigns import _text_cell
from ymva.api.schemas import AuditLogOut, Page
from ymva.db import get_sessionmaker
from ymva.models import AuditLog
from ymva.services import audit

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut])
async def list_audit_logs(
    _: AuditViewer,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    entity_type: str | None = None,
) -> dict[str, object]:
    query = select(AuditLog)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = await db.scalars(query.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset))
    return {"items": list(rows), "total": total or 0, "limit": limit, "offset": offset}


EXPORT_COLUMNS = ["time_utc", "who", "action", "entity_type", "entity_id", "summary", "ip_address",
                  "changes"]  # fmt: skip


@router.get("/export.csv")
async def export_audit_logs(
    user: AuditViewer, request: Request, db: DbSession
) -> StreamingResponse:
    """The whole audit log as one CSV, oldest first. Streamed, so a long history doesn't time
    out; the export itself is logged."""
    await audit.record(
        db,
        actor=user,
        action="audit.export",
        entity_type="audit_log",
        entity_id=None,
        summary="Exported the audit log",
        ip_address=client_ip(request),
    )
    await db.commit()

    async def rows() -> AsyncIterator[str]:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(EXPORT_COLUMNS)
        async with get_sessionmaker()() as session:
            result = await session.stream_scalars(
                select(AuditLog).order_by(AuditLog.created_at), execution_options={"yield_per": 500}
            )
            async for log in result:
                writer.writerow(
                    [
                        log.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                        _text_cell(log.actor_label),
                        log.action,
                        log.entity_type,
                        log.entity_id or "",
                        _text_cell(log.summary),
                        log.ip_address or "",
                        _text_cell(json.dumps(log.changes or {}, ensure_ascii=False)),
                    ]
                )
                if out.tell() > 64_000:
                    yield out.getvalue()
                    out.seek(0)
                    out.truncate()
        yield out.getvalue()

    name = f"ymva-audit-log-{datetime.now(UTC):%Y-%m-%d}.csv"
    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
