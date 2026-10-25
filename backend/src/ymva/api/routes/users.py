import uuid

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import delete, func, select

from ymva.api.deps import AdminUser, DbSession, client_ip
from ymva.api.schemas import UserCreateIn, UserOut, UserUpdateIn
from ymva.models import Role, User, UserSession
from ymva.security import hash_password
from ymva.services import audit

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
async def list_users(_: AdminUser, db: DbSession) -> list[User]:
    return list(await db.scalars(select(User).order_by(User.created_at)))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateIn, request: Request, admin: AdminUser, db: DbSession
) -> User:
    email = body.email.strip().lower()
    if await db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    user = User(
        email=email,
        name=body.name.strip(),
        role=body.role,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    await db.flush()
    await audit.record(
        db,
        actor=admin,
        action="user.create",
        entity_type="user",
        entity_id=user.id,
        summary=f"Added {user.email} as {user.role.value}",
        ip_address=client_ip(request),
    )
    await db.commit()
    return user


async def _active_admin_count(db: DbSession) -> int:
    return int(
        await db.scalar(
            select(func.count()).select_from(User).where(User.role == Role.ADMIN, User.is_active)
        )
        or 0
    )


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID, body: UserUpdateIn, request: Request, admin: AdminUser, db: DbSession
) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    losing_admin = (
        user.role == Role.ADMIN
        and user.is_active
        and ((body.role is not None and body.role != Role.ADMIN) or body.is_active is False)
    )
    if losing_admin and await _active_admin_count(db) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one active admin is required")

    changes: dict[str, object] = {}
    if body.name is not None and body.name.strip() != user.name:
        changes["name"] = [user.name, body.name.strip()]
        user.name = body.name.strip()
    if body.role is not None and body.role != user.role:
        changes["role"] = [user.role.value, body.role.value]
        user.role = body.role
    if body.is_active is not None and body.is_active != user.is_active:
        changes["is_active"] = [user.is_active, body.is_active]
        user.is_active = body.is_active
    if body.password is not None:
        changes["password_reset"] = True
        user.password_hash = hash_password(body.password)

    if "password_reset" in changes or changes.get("is_active") == [True, False]:
        await db.execute(delete(UserSession).where(UserSession.user_id == user.id))

    if changes:
        await audit.record(
            db,
            actor=admin,
            action="user.update",
            entity_type="user",
            entity_id=user.id,
            summary=f"Updated {user.email}",
            changes=changes,
            ip_address=client_ip(request),
        )
    await db.commit()
    return user
