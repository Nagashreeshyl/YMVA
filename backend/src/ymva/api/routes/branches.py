"""Branches and their managers: calls are transferred to the customer's branch manager."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ymva.api.deps import CurrentUser, DbSession, SettingsManager, client_ip
from ymva.api.routes.numbers import normalise_e164
from ymva.models import Branch
from ymva.services import audit

router = APIRouter(prefix="/branches", tags=["branches"])


class BranchIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    other_names: list[str] = Field(default_factory=list, max_length=20)
    manager_name: str = Field(default="", max_length=120)
    manager_phone: str

    @field_validator("manager_phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalise_e164(v)

    @field_validator("name", "manager_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("other_names")
    @classmethod
    def _names(cls, v: list[str]) -> list[str]:
        return [n.strip()[:80] for n in v if n.strip()]


class BranchOut(BaseModel):
    id: uuid.UUID
    name: str
    other_names: list[str]
    manager_name: str
    manager_phone: str
    updated_at: datetime


def _out(b: Branch) -> BranchOut:
    return BranchOut(
        id=b.id,
        name=b.name,
        other_names=b.other_names,
        manager_name=b.manager_name,
        manager_phone=b.manager_phone,
        updated_at=b.updated_at,
    )


@router.get("")
async def list_branches(_: CurrentUser, db: DbSession) -> list[BranchOut]:
    """Everyone who starts calls (n8n, the dashboard) picks a customer's branch from here."""
    return [_out(b) for b in await db.scalars(select(Branch).order_by(Branch.name))]


async def _save(db: DbSession, branch: Branch) -> None:
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "A branch with that name exists") from exc


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_branch(
    body: BranchIn, request: Request, admin: SettingsManager, db: DbSession
) -> BranchOut:
    branch = Branch(**body.model_dump())
    db.add(branch)
    await _save(db, branch)
    await audit.record(
        db,
        actor=admin,
        action="branch.create",
        entity_type="branch",
        entity_id=branch.id,
        summary=f"Added branch {branch.name} (manager {branch.manager_phone})",
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(branch)
    return _out(branch)


@router.put("/{branch_id}")
async def update_branch(
    branch_id: uuid.UUID, body: BranchIn, request: Request, admin: SettingsManager, db: DbSession
) -> BranchOut:
    branch = await db.get(Branch, branch_id)
    if branch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Branch not found")
    for field, value in body.model_dump().items():
        setattr(branch, field, value)
    await _save(db, branch)
    await audit.record(
        db,
        actor=admin,
        action="branch.update",
        entity_type="branch",
        entity_id=branch.id,
        summary=f"Updated branch {branch.name} (manager {branch.manager_phone})",
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(branch)
    return _out(branch)


@router.delete("/{branch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_branch(
    branch_id: uuid.UUID, request: Request, admin: SettingsManager, db: DbSession
) -> None:
    branch = await db.get(Branch, branch_id)
    if branch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Branch not found")
    await audit.record(
        db,
        actor=admin,
        action="branch.delete",
        entity_type="branch",
        entity_id=branch.id,
        summary=f"Removed branch {branch.name}",
        ip_address=client_ip(request),
    )
    await db.delete(branch)
    await db.commit()
