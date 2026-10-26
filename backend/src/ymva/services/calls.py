"""Call orchestration shared by telephony, outbound and browser calls."""

import secrets
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.config import get_settings
from ymva.models import Agent, AgentVersion, Call, CallStatus
from ymva.redis import get_redis
from ymva.voice.config import AgentConfig

ACTIVE_STATUSES = (CallStatus.QUEUED, CallStatus.RINGING, CallStatus.IN_PROGRESS)
WS_TOKEN_TTL_SECONDS = 180

# Calls running in this process, so they can be ended from the dashboard.
ACTIVE_SESSIONS: dict[uuid.UUID, Any] = {}


class CallSetupError(Exception):
    """Reason a call can't start; safe to show to admins."""


class LinesBusyError(CallSetupError):
    """The platform-wide concurrent call limit is reached; try again shortly."""


async def runnable_version(
    session: AsyncSession, agent: Agent, *, draft: bool = False
) -> tuple[AgentVersion, AgentConfig]:
    """Published version for real calls; latest version for browser tests (draft=True)."""
    if draft:
        version = await session.scalar(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent.id)
            .order_by(AgentVersion.version.desc())
            .limit(1)
        )
    else:
        version = (
            await session.get(AgentVersion, agent.published_version_id)
            if agent.published_version_id
            else None
        )
    if version is None:
        raise CallSetupError(
            f"{agent.name} has no {'saved' if draft else 'published'} version yet."
        )
    return version, AgentConfig.model_validate(version.config)


async def check_capacity(session: AsyncSession) -> None:
    in_progress = await session.scalar(
        select(func.count()).select_from(Call).where(Call.status == CallStatus.IN_PROGRESS)
    )
    if (in_progress or 0) >= get_settings().max_concurrent_calls:
        raise LinesBusyError("All lines are busy right now (concurrent call limit reached).")


async def issue_stream_token(call_id: uuid.UUID) -> str:
    token = secrets.token_urlsafe(24)
    await get_redis().set(f"ws-token:{token}", str(call_id), ex=WS_TOKEN_TTL_SECONDS)
    return token


async def redeem_stream_token(token: str) -> uuid.UUID | None:
    value = await get_redis().getdel(f"ws-token:{token}")
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def string_variables(raw: dict[str, Any]) -> dict[str, str]:
    return {str(k): "" if v is None else str(v) for k, v in raw.items()}
