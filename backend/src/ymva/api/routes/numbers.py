import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from ymva.api.deps import DbSession, NumberManager, NumberViewer, client_ip
from ymva.models import Agent, PhoneNumber, ProviderAccount
from ymva.services import audit

router = APIRouter(prefix="/numbers", tags=["numbers"])


def normalise_e164(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits.startswith("00"):  # international dialling prefix, e.g. 0091...
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):  # Indian trunk prefix, e.g. 080 ...
        digits = digits[1:]
    elif len(digits) == 13 and digits.startswith("910"):  # +91 0 80 ..., same number
        digits = "91" + digits[3:]
    if len(digits) == 10:  # Indian national number without country code
        digits = "91" + digits
    if not 11 <= len(digits) <= 15:
        raise ValueError("Enter the full number with country code, e.g. +91 80 1234 5678")
    return "+" + digits


class NumberOut(BaseModel):
    id: uuid.UUID
    e164: str
    label: str
    provider_account_id: uuid.UUID
    provider: str
    provider_account_name: str
    inbound_agent_id: uuid.UUID | None
    inbound_agent_name: str | None
    max_concurrent_calls: int
    is_active: bool
    created_at: datetime


class NumberIn(BaseModel):
    e164: str
    label: str = Field(default="", max_length=120)
    provider_account_id: uuid.UUID
    inbound_agent_id: uuid.UUID | None = None
    max_concurrent_calls: int = Field(default=5, ge=1, le=200)

    _normalise = field_validator("e164")(normalise_e164)


class NumberPatch(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    inbound_agent_id: uuid.UUID | None = None
    clear_inbound_agent: bool = False
    max_concurrent_calls: int | None = Field(default=None, ge=1, le=200)
    is_active: bool | None = None


async def _out(db: DbSession, number: PhoneNumber) -> NumberOut:
    account = await db.get(ProviderAccount, number.provider_account_id)
    agent = await db.get(Agent, number.inbound_agent_id) if number.inbound_agent_id else None
    assert account is not None
    return NumberOut(
        id=number.id,
        e164=number.e164,
        label=number.label,
        provider_account_id=account.id,
        provider=account.provider.value,
        provider_account_name=account.name,
        inbound_agent_id=number.inbound_agent_id,
        inbound_agent_name=agent.name if agent else None,
        max_concurrent_calls=number.max_concurrent_calls,
        is_active=number.is_active,
        created_at=number.created_at,
    )


async def _check_agent(db: DbSession, agent_id: uuid.UUID | None) -> None:
    if agent_id and await db.get(Agent, agent_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Agent not found")


@router.get("", response_model=list[NumberOut])
async def list_numbers(_: NumberViewer, db: DbSession) -> list[NumberOut]:
    numbers = await db.scalars(select(PhoneNumber).order_by(PhoneNumber.created_at))
    return [await _out(db, n) for n in numbers]


@router.post("", response_model=NumberOut, status_code=status.HTTP_201_CREATED)
async def add_number(
    body: NumberIn, request: Request, admin: NumberManager, db: DbSession
) -> NumberOut:
    if await db.get(ProviderAccount, body.provider_account_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Telephony account not found")
    await _check_agent(db, body.inbound_agent_id)
    if await db.scalar(select(PhoneNumber.id).where(PhoneNumber.e164 == body.e164)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This number is already added")
    number = PhoneNumber(**body.model_dump())
    db.add(number)
    await db.flush()
    await audit.record(
        db,
        actor=admin,
        action="number.add",
        entity_type="phone_number",
        entity_id=number.id,
        summary=f"Added number {number.e164}",
        ip_address=client_ip(request),
    )
    await db.commit()
    return await _out(db, number)


@router.patch("/{number_id}", response_model=NumberOut)
async def update_number(
    number_id: uuid.UUID, body: NumberPatch, request: Request, admin: NumberManager, db: DbSession
) -> NumberOut:
    number = await db.get(PhoneNumber, number_id)
    if number is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Number not found")
    changes: dict[str, object] = {}
    if body.label is not None:
        changes["label"] = [number.label, body.label]
        number.label = body.label
    if body.clear_inbound_agent or body.inbound_agent_id is not None:
        await _check_agent(db, body.inbound_agent_id)
        new_agent = None if body.clear_inbound_agent else body.inbound_agent_id
        changes["inbound_agent"] = [str(number.inbound_agent_id), str(new_agent)]
        number.inbound_agent_id = new_agent
    if body.max_concurrent_calls is not None:
        changes["max_concurrent_calls"] = [number.max_concurrent_calls, body.max_concurrent_calls]
        number.max_concurrent_calls = body.max_concurrent_calls
    if body.is_active is not None:
        changes["is_active"] = [number.is_active, body.is_active]
        number.is_active = body.is_active
    if changes:
        await audit.record(
            db,
            actor=admin,
            action="number.update",
            entity_type="phone_number",
            entity_id=number.id,
            summary=f"Updated number {number.e164}",
            changes=changes,
            ip_address=client_ip(request),
        )
    await db.commit()
    return await _out(db, number)


@router.delete("/{number_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_number(
    number_id: uuid.UUID, request: Request, admin: NumberManager, db: DbSession
) -> None:
    number = await db.get(PhoneNumber, number_id)
    if number is None:
        return
    await db.delete(number)
    await audit.record(
        db,
        actor=admin,
        action="number.remove",
        entity_type="phone_number",
        entity_id=number.id,
        summary=f"Removed number {number.e164}",
        ip_address=client_ip(request),
    )
    await db.commit()
