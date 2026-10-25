from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ymva.models import AuditLog, User


async def record(
    session: AsyncSession,
    *,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: object | None,
    summary: str,
    changes: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    """Add an audit entry to the current transaction; the caller commits."""
    session.add(
        AuditLog(
            actor_user_id=actor.id if actor else None,
            actor_label=actor.email if actor else "system",
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            summary=summary,
            changes=changes or {},
            ip_address=ip_address,
        )
    )
