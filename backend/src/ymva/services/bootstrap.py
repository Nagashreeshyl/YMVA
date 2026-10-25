"""Keeps the admin account defined in .env (ADMIN_EMAIL / ADMIN_NAME / ADMIN_PASSWORD) usable."""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.config import get_settings
from ymva.logging import get_logger
from ymva.models import AuditLog, Role, User, UserSession
from ymva.security import MIN_PASSWORD_LENGTH, hash_password
from ymva.services import audit

log = get_logger(__name__)


def _env_admin() -> tuple[str, str, str] | None:
    settings = get_settings()
    password = settings.admin_password.get_secret_value()
    if len(password) < MIN_PASSWORD_LENGTH:
        log.warning(
            f"ADMIN_PASSWORD is missing or shorter than {MIN_PASSWORD_LENGTH} characters; "
            "skipping admin setup"
        )
        return None
    return settings.admin_email.strip().lower(), settings.admin_name.strip(), password


async def _bootstrap_admin(session: AsyncSession) -> User | None:
    """The account created on first start, if it still exists."""
    entity_id = await session.scalar(
        select(AuditLog.entity_id)
        .where(AuditLog.action == "user.bootstrap")
        .order_by(AuditLog.created_at.desc())
        .limit(1)
    )
    return await session.get(User, uuid.UUID(entity_id)) if entity_id else None


async def ensure_admin(session: AsyncSession) -> None:
    """Run on startup.

    - No users yet: create the admin from .env.
    - The .env admin email doesn't exist yet (ADMIN_EMAIL was edited): move the first-start
      admin account to the new email, name and password, so .env stays the source of truth.
    - Otherwise leave accounts alone (passwords changed in the dashboard are kept).
    """
    env = _env_admin()
    if env is None:
        return
    email, name, password = env

    if not await session.scalar(select(func.count()).select_from(User)):
        admin = User(email=email, name=name, password_hash=hash_password(password), role=Role.ADMIN)
        session.add(admin)
        await session.flush()
        await audit.record(
            session,
            actor=None,
            action="user.bootstrap",
            entity_type="user",
            entity_id=admin.id,
            summary=f"Created first admin {admin.email}",
        )
        await session.commit()
        log.info("bootstrap admin created", email=admin.email)
        return

    if await session.scalar(select(User.id).where(User.email == email)):
        return

    original = await _bootstrap_admin(session)
    if original is None:
        log.warning(
            "ADMIN_EMAIL from .env has no account and the first admin no longer exists; "
            "run `./ymva reset-admin` to create it"
        )
        return
    changes = {"email": [original.email, email], "name": [original.name, name]}
    original.email = email
    original.name = name
    original.password_hash = hash_password(password)
    original.role = Role.ADMIN
    original.is_active = True
    await session.execute(delete(UserSession).where(UserSession.user_id == original.id))
    await audit.record(
        session,
        actor=None,
        action="user.admin_sync",
        entity_type="user",
        entity_id=original.id,
        summary=f"Admin account updated from .env to {email}",
        changes={**changes, "password_reset": True},
    )
    await session.commit()
    log.info("admin account updated from .env", email=email)


async def reset_admin(session: AsyncSession) -> str:
    """Make the .env admin exist, be active, and use the .env password. Returns a message."""
    env = _env_admin()
    if env is None:
        return f"ADMIN_PASSWORD in .env must be at least {MIN_PASSWORD_LENGTH} characters."
    email, name, password = env
    user = await session.scalar(select(User).where(User.email == email))
    created = user is None
    if user is None:
        user = User(email=email, name=name, password_hash="", role=Role.ADMIN)
        session.add(user)
    user.password_hash = hash_password(password)
    user.role = Role.ADMIN
    user.is_active = True
    await session.flush()
    await session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await audit.record(
        session,
        actor=None,
        action="user.admin_reset",
        entity_type="user",
        entity_id=user.id,
        summary=f"{'Created' if created else 'Reset'} admin {email} from the command line",
    )
    await session.commit()
    verb = "Created" if created else "Reset"
    return f"{verb} admin {email}. Sign in with the ADMIN_PASSWORD from .env."
