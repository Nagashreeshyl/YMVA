from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import delete, select

from ymva.api.deps import SESSION_COOKIE, CurrentUser, DbSession, client_ip
from ymva.api.schemas import ChangePasswordIn, LoginIn, UserOut
from ymva.config import get_settings
from ymva.models import User, UserSession
from ymva.redis import get_redis
from ymva.security import (
    hash_password,
    hash_token,
    new_token,
    password_needs_rehash,
    verify_password,
)
from ymva.services import audit, ratelimit

router = APIRouter(prefix="/auth", tags=["auth"])

# Verified against when the email is unknown, so response timing doesn't reveal accounts.
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


@router.post("/login", response_model=UserOut)
async def login(body: LoginIn, request: Request, response: Response, db: DbSession) -> User:
    settings = get_settings()
    email = body.email.strip().lower()
    ip = client_ip(request)
    limit_key = f"login:{email}:{ip}"
    redis = get_redis()
    allowed = await ratelimit.hit(
        redis, limit_key, settings.login_max_attempts, settings.login_window_seconds
    )
    account_allowed = await ratelimit.hit(
        redis,
        f"login-account:{email}",
        settings.login_max_attempts_per_account,
        settings.login_window_seconds,
    )
    if not (allowed and account_allowed):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many sign-in attempts. Try again later."
        )

    user = await db.scalar(select(User).where(User.email == email))
    valid = verify_password(user.password_hash if user else _DUMMY_HASH, body.password)
    if user is None or not valid or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")

    await ratelimit.reset(redis, limit_key)
    await ratelimit.reset(redis, f"login-account:{email}")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)

    token = new_token()
    now = datetime.now(UTC)
    ttl = timedelta(hours=settings.session_ttl_hours)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=now + ttl,
            last_seen_at=now,
            ip_address=ip,
            user_agent=(request.headers.get("user-agent") or "")[:400],
        )
    )
    user.last_login_at = now
    await db.execute(delete(UserSession).where(UserSession.expires_at < now))
    await audit.record(
        db,
        actor=user,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        summary=f"{user.email} signed in",
        ip_address=ip,
    )
    await db.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbSession) -> None:
    if token := request.cookies.get(SESSION_COOKIE):
        await db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))
        await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordIn, request: Request, user: CurrentUser, db: DbSession
) -> None:
    if not getattr(user, "password_hash", None):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "API keys cannot change passwords")
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    # Sign out every other session for this user.
    current = hash_token(request.cookies.get(SESSION_COOKIE, ""))
    await db.execute(
        delete(UserSession).where(UserSession.user_id == user.id, UserSession.token_hash != current)
    )
    await audit.record(
        db,
        actor=user,
        action="auth.change_password",
        entity_type="user",
        entity_id=user.id,
        summary=f"{user.email} changed their password",
        ip_address=client_ip(request),
    )
    await db.commit()
