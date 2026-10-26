"""Do-not-call list: numbers that campaigns and "Call a phone" must never dial."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ymva.api.deps import AdminUser, CurrentUser, DbSession, client_ip
from ymva.api.routes.numbers import normalise_e164
from ymva.models import DoNotCall
from ymva.services import audit

router = APIRouter(prefix="/do-not-call", tags=["do-not-call"])


class DoNotCallOut(BaseModel):
    phone: str
    reason: str
    created_at: datetime


class DoNotCallIn(BaseModel):
    # One or more numbers, separated by new lines, commas or spaces between numbers.
    numbers: str = Field(min_length=1, max_length=200_000)
    reason: str = Field(default="", max_length=200)


@router.get("")
async def list_numbers(_: CurrentUser, db: DbSession) -> list[DoNotCallOut]:
    rows = await db.scalars(select(DoNotCall).order_by(DoNotCall.created_at.desc()))
    return [DoNotCallOut(phone=r.phone, reason=r.reason, created_at=r.created_at) for r in rows]


@router.post("")
async def add_numbers(
    body: DoNotCallIn, request: Request, admin: AdminUser, db: DbSession
) -> dict[str, int]:
    phones, bad = set(), []
    for raw in body.numbers.replace(",", "\n").replace(";", "\n").splitlines():
        if raw.strip():
            try:
                phones.add(normalise_e164(raw))
            except ValueError:
                bad.append(raw.strip())
    if bad:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"These don't look like phone numbers: {', '.join(bad[:5])}"
            + (f" and {len(bad) - 5} more" if len(bad) > 5 else ""),
        )
    if phones:
        await db.execute(
            insert(DoNotCall)
            .values([{"phone": p, "reason": body.reason.strip()} for p in phones])
            .on_conflict_do_nothing()
        )
    await audit.record(
        db,
        actor=admin,
        action="do_not_call.add",
        entity_type="do_not_call",
        entity_id=None,
        summary=f"Added {len(phones)} number(s) to the do-not-call list",
        ip_address=client_ip(request),
    )
    await db.commit()
    return {"added": len(phones)}


@router.delete("/{phone}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_number(phone: str, request: Request, admin: AdminUser, db: DbSession) -> None:
    result = await db.execute(delete(DoNotCall).where(DoNotCall.phone == phone))
    if not result.rowcount:  # type: ignore[attr-defined]
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not on the list")
    await audit.record(
        db,
        actor=admin,
        action="do_not_call.remove",
        entity_type="do_not_call",
        entity_id=None,
        summary=f"Removed {phone} from the do-not-call list",
        ip_address=client_ip(request),
    )
    await db.commit()
