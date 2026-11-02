"""API keys for other apps (n8n, CRMs): `Authorization: Bearer ymva_...`. A key acts with its
role's permissions, so a Caller key can only start calls and read the calls it started."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ymva.api.deps import API_KEY_PREFIX, DbSession, UserManager, client_ip
from ymva.models import ApiKey, Role
from ymva.permissions import Perm, can
from ymva.security import hash_token, new_token
from ymva.services import audit

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: Role = Role.CALLER


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    role: Role
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class NewApiKeyOut(ApiKeyOut):
    key: str  # shown once; only its hash is stored


def _out(key: ApiKey) -> dict[str, object]:
    return {f: getattr(key, f) for f in ApiKeyOut.model_fields}


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(_: UserManager, db: DbSession) -> list[dict[str, object]]:
    keys = await db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc()))
    return [_out(k) for k in keys]


@router.post("", response_model=NewApiKeyOut, status_code=status.HTTP_201_CREATED)
async def create_key(
    body: ApiKeyIn, request: Request, admin: UserManager, db: DbSession
) -> dict[str, object]:
    if body.role == Role.OWNER and not can(admin.role, Perm.OWNERS_MANAGE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner can make an owner key")
    token = new_token(API_KEY_PREFIX)
    key = ApiKey(
        name=body.name.strip(),
        key_prefix=token[:12],
        key_hash=hash_token(token),
        role=body.role,
        created_by_id=admin.id,
    )
    db.add(key)
    await db.flush()
    await audit.record(
        db,
        actor=admin,
        action="api_key.create",
        entity_type="api_key",
        entity_id=key.id,
        summary=f"Created API key '{key.name}' ({key.role.value})",
        ip_address=client_ip(request),
    )
    await db.commit()
    return {**_out(key), "key": token}


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: uuid.UUID, request: Request, admin: UserManager, db: DbSession
) -> None:
    key = await db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    if key.role == Role.OWNER and not can(admin.role, Perm.OWNERS_MANAGE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner can revoke an owner key")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        await audit.record(
            db,
            actor=admin,
            action="api_key.revoke",
            entity_type="api_key",
            entity_id=key.id,
            summary=f"Revoked API key '{key.name}'",
            ip_address=client_ip(request),
        )
        await db.commit()
