from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.db import get_session
from ymva.models import ApiKey, User, UserSession
from ymva.permissions import Perm, can
from ymva.security import hash_token

SESSION_COOKIE = "ymva_session"
API_KEY_PREFIX = "ymva_"

DbSession = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str | None:
    # Uvicorn already resolves X-Forwarded-For, but only from trusted proxies (FORWARDED_ALLOW_IPS),
    # so a client can't spoof its address to dodge rate limits.
    return request.client.host if request.client else None


async def _user_from_session(db: AsyncSession, token: str) -> User | None:
    now = datetime.now(UTC)
    user_session = await db.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_token(token), UserSession.expires_at > now
        )
    )
    if user_session is None or not user_session.user.is_active:
        return None
    # Throttle last-seen writes to once a minute per session.
    if user_session.last_seen_at is None or now - user_session.last_seen_at > timedelta(minutes=1):
        user_session.last_seen_at = now
        await db.commit()
    return user_session.user


async def _user_from_api_key(db: AsyncSession, token: str) -> User | None:
    key = await db.scalar(
        select(ApiKey).where(ApiKey.key_hash == hash_token(token), ApiKey.revoked_at.is_(None))
    )
    if key is None:
        return None
    key.last_used_at = datetime.now(UTC)
    await db.commit()
    # API keys act as a synthetic, non-persisted user carrying the key's role.
    return User(
        id=key.created_by_id,
        email=f"api-key:{key.name}",
        name=key.name,
        role=key.role,
        is_active=True,
        created_at=key.created_at,
    )


def is_api_key_header(auth: str) -> bool:
    return auth.lower().startswith("bearer ") and auth[7:].startswith(API_KEY_PREFIX)


async def current_user(request: Request, db: DbSession) -> User:
    user: User | None = None
    auth = request.headers.get("authorization", "")
    if auth:
        # A request that brings its own credentials is judged on them alone, never on a
        # cookie the browser added (that would let a forged header skip the origin check).
        user = await _user_from_api_key(db, auth[7:]) if is_api_key_header(auth) else None
    elif token := request.cookies.get(SESSION_COOKIE):
        user = await _user_from_session(db, token)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


def allowed(*perms: Perm) -> Any:
    """A signed-in user whose role has any of `perms`."""

    async def check(user: Annotated[User, Depends(current_user)]) -> User:
        if not any(can(user.role, p) for p in perms):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role doesn't allow this")
        return user

    return Depends(check)


CurrentUser = Annotated[User, Depends(current_user)]

P = Perm
AgentEditor = Annotated[User, allowed(P.AGENTS_EDIT)]
AgentViewer = Annotated[User, allowed(P.AGENTS_VIEW)]
# Callers open an agent only to call from it.
AgentOpener = Annotated[User, allowed(P.AGENTS_VIEW, P.CALLS_PLACE)]
CallPlacer = Annotated[User, allowed(P.CALLS_PLACE)]
CallEditor = Annotated[User, allowed(P.CALLS_EDIT)]
RecordingListener = Annotated[User, allowed(P.RECORDINGS_LISTEN)]
CampaignViewer = Annotated[User, allowed(P.CAMPAIGNS_VIEW)]
CampaignManager = Annotated[User, allowed(P.CAMPAIGNS_MANAGE)]
NumberViewer = Annotated[User, allowed(P.NUMBERS_VIEW)]
NumberManager = Annotated[User, allowed(P.NUMBERS_MANAGE)]
# The agent editor shows the tools an agent uses, read-only for viewers.
ToolReader = Annotated[User, allowed(P.TOOLS_MANAGE, P.AGENTS_VIEW)]
ToolManager = Annotated[User, allowed(P.TOOLS_MANAGE)]
IntegrationManager = Annotated[User, allowed(P.INTEGRATIONS_MANAGE)]
SettingsManager = Annotated[User, allowed(P.SETTINGS_MANAGE)]
DncManager = Annotated[User, allowed(P.DNC_MANAGE)]
UserManager = Annotated[User, allowed(P.USERS_MANAGE)]
AuditViewer = Annotated[User, allowed(P.AUDIT_VIEW)]
