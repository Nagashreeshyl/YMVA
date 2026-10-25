from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ymva.api.deps import CurrentUser, DbSession
from ymva.api.schemas import AuditLogOut, Page
from ymva.models import AuditLog

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut])
async def list_audit_logs(
    _: CurrentUser,
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
