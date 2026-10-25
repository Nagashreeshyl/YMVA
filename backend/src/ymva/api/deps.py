from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.db import get_session
from ymva.models import ApiKey, Role, User, UserSession
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
    return User(id=key.created_by_id, email=f"api-key:{key.name}", name=key.name, role=key.role)


async def current_user(request: Request, db: DbSession) -> User:
    user: User | None = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and auth[7:].startswith(API_KEY_PREFIX):
        user = await _user_from_api_key(db, auth[7:])
    elif token := request.cookies.get(SESSION_COOKIE):
        user = await _user_from_session(db, token)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


async def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
