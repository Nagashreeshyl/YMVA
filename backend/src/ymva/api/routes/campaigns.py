"""Campaigns: an agent calls a list of contacts within calling hours (see services/dialer.py)."""

import csv
import io
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from ymva.api.deps import CampaignManager, CampaignViewer, DbSession, client_ip
from ymva.api.routes.numbers import normalise_e164
from ymva.models import (
    Agent,
    Call,
    Campaign,
    CampaignContact,
    CampaignStatus,
    ContactStatus,
    PhoneNumber,
)
from ymva.services import audit
from ymva.services.calls import CallSetupError, runnable_version

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

MAX_IMPORT_BYTES = 5_000_000
MAX_CONTACTS = 50_000
PHONE_HEADERS = ("phone", "phone_number", "mobile", "mobile_number", "number", "contact", "msisdn")
TIME_RE = r"^([01]\d|2[0-3]):[0-5]\d$"


class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    agent_id: uuid.UUID
    phone_number_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5], min_length=1)
    start_time: str = Field(default="08:00", pattern=TIME_RE)
    end_time: str = Field(default="19:00", pattern=TIME_RE)
    max_concurrent: int = Field(default=3, ge=1, le=50)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_after_minutes: int = Field(default=60, ge=5, le=1440)

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d not in range(7) for d in v):
            raise ValueError("Days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))


class CampaignOut(CampaignIn):
    id: uuid.UUID
    agent_name: str | None
    status: CampaignStatus
    last_error: str | None
    counts: dict[str, int]  # contacts by status, plus "total"
    outcomes: dict[str, int]  # outcomes of the calls that reached people
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class ImportIn(BaseModel):
    # Either the CSV text, or the link of a Google Sheet shared as "anyone with the link".
    csv: str | None = Field(default=None, max_length=MAX_IMPORT_BYTES)
    sheet_url: str | None = Field(default=None, max_length=500)
    phone_column: str | None = Field(default=None, max_length=100)


class ContactOut(BaseModel):
    id: uuid.UUID
    phone: str
    variables: dict[str, Any]
    status: ContactStatus
    attempts: int
    last_result: str | None
    next_attempt_at: datetime | None
    call_id: uuid.UUID | None
    outcome: str | None
    summary: str | None
    extracted: dict[str, Any]


def _snake(header: str) -> str:
    """'Customer Name' -> customer_name, so columns match the agent's {{placeholders}}."""
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


async def _out(db: DbSession, campaign: Campaign) -> CampaignOut:
    counts = dict(
        (
            await db.execute(
                select(CampaignContact.status, func.count())
                .where(CampaignContact.campaign_id == campaign.id)
                .group_by(CampaignContact.status)
            )
        ).all()
    )
    outcomes = dict(
        (
            await db.execute(
                select(Call.outcome, func.count())
                .join(CampaignContact, CampaignContact.last_call_id == Call.id)
                .where(
                    CampaignContact.campaign_id == campaign.id,
                    CampaignContact.status == ContactStatus.DONE,
                    Call.outcome.is_not(None),
                )
                .group_by(Call.outcome)
            )
        ).all()
    )
    agent = await db.get(Agent, campaign.agent_id) if campaign.agent_id else None
    by_status = {s.value: int(counts.get(s, 0)) for s in ContactStatus}
    return CampaignOut(
        id=campaign.id,
        name=campaign.name,
        agent_id=campaign.agent_id or uuid.UUID(int=0),
        agent_name=agent.name if agent else None,
        phone_number_ids=[uuid.UUID(i) for i in campaign.phone_number_ids],
        days=campaign.days,
        start_time=campaign.start_time,
        end_time=campaign.end_time,
        max_concurrent=campaign.max_concurrent,
        max_attempts=campaign.max_attempts,
        retry_after_minutes=campaign.retry_after_minutes,
        status=campaign.status,
        last_error=campaign.last_error,
        counts=by_status | {"total": sum(by_status.values())},
        outcomes={str(k): int(v) for k, v in outcomes.items()},
        started_at=campaign.started_at,
        completed_at=campaign.completed_at,
        created_at=campaign.created_at,
    )


async def _get(db: DbSession, campaign_id: uuid.UUID) -> Campaign:
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campaign not found")
    return campaign


async def _check_refs(db: DbSession, body: CampaignIn) -> None:
    if await db.get(Agent, body.agent_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose an agent")
    found = await db.scalar(
        select(func.count())
        .select_from(PhoneNumber)
        .where(PhoneNumber.id.in_(body.phone_number_ids))
    )
    if found != len(set(body.phone_number_ids)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose numbers to call from")
    if body.start_time >= body.end_time:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Calling hours must end after they start"
        )


def _apply(campaign: Campaign, body: CampaignIn) -> None:
    campaign.name, campaign.agent_id = body.name.strip(), body.agent_id
    campaign.phone_number_ids = [str(i) for i in dict.fromkeys(body.phone_number_ids)]
    campaign.days, campaign.start_time, campaign.end_time = (
        body.days,
        body.start_time,
        body.end_time,
    )
    campaign.max_concurrent, campaign.max_attempts = body.max_concurrent, body.max_attempts
    campaign.retry_after_minutes = body.retry_after_minutes


@router.get("", response_model=list[CampaignOut])
async def list_campaigns(_: CampaignViewer, db: DbSession) -> list[CampaignOut]:
    campaigns = await db.scalars(select(Campaign).order_by(Campaign.created_at.desc()))
    return [await _out(db, c) for c in campaigns]


@router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    body: CampaignIn, request: Request, admin: CampaignManager, db: DbSession
) -> CampaignOut:
    await _check_refs(db, body)
    campaign = Campaign(created_by_id=admin.id)
    _apply(campaign, body)
    db.add(campaign)
    await db.flush()
    await audit.record(
        db,
        actor=admin,
        action="campaign.create",
        entity_type="campaign",
        entity_id=campaign.id,
        summary=f"Created campaign '{campaign.name}'",
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(campaign)
    return await _out(db, campaign)


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: uuid.UUID, _: CampaignViewer, db: DbSession) -> CampaignOut:
    return await _out(db, await _get(db, campaign_id))


@router.put("/{campaign_id}", response_model=CampaignOut)
async def update_campaign(
    campaign_id: uuid.UUID,
    body: CampaignIn,
    request: Request,
    admin: CampaignManager,
    db: DbSession,
) -> CampaignOut:
    campaign = await _get(db, campaign_id)
    await _check_refs(db, body)
    _apply(campaign, body)
    await audit.record(
        db,
        actor=admin,
        action="campaign.update",
        entity_type="campaign",
        entity_id=campaign.id,
        summary=f"Updated campaign '{campaign.name}'",
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(campaign)
    return await _out(db, campaign)


@router.post("/{campaign_id}/start", response_model=CampaignOut)
async def start_campaign(
    campaign_id: uuid.UUID, request: Request, admin: CampaignManager, db: DbSession
) -> CampaignOut:
    campaign = await _get(db, campaign_id)
    agent = await db.get(Agent, campaign.agent_id) if campaign.agent_id else None
    if agent is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The campaign's agent was deleted")
    try:
        await runnable_version(db, agent)  # calls use the published version
    except CallSetupError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    waiting = await db.scalar(
        select(func.count())
        .select_from(CampaignContact)
        .where(
            CampaignContact.campaign_id == campaign.id,
            CampaignContact.status.in_([ContactStatus.PENDING, ContactStatus.RETRY]),
        )
    )
    if not waiting:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add contacts to call first")
    campaign.status, campaign.last_error, campaign.completed_at = CampaignStatus.RUNNING, None, None
    campaign.started_at = campaign.started_at or datetime.now(UTC)
    await audit.record(
        db,
        actor=admin,
        action="campaign.start",
        entity_type="campaign",
        entity_id=campaign.id,
        summary=f"Started campaign '{campaign.name}'",
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(campaign)
    return await _out(db, campaign)


@router.post("/{campaign_id}/pause", response_model=CampaignOut)
async def pause_campaign(
    campaign_id: uuid.UUID, request: Request, admin: CampaignManager, db: DbSession
) -> CampaignOut:
    """Stop placing new calls; calls already in progress finish normally."""
    campaign = await _get(db, campaign_id)
    if campaign.status == CampaignStatus.RUNNING:
        campaign.status = CampaignStatus.PAUSED
        await audit.record(
            db,
            actor=admin,
            action="campaign.pause",
            entity_type="campaign",
            entity_id=campaign.id,
            summary=f"Paused campaign '{campaign.name}'",
            ip_address=client_ip(request),
        )
        await db.commit()
        await db.refresh(campaign)
    return await _out(db, campaign)


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(
    campaign_id: uuid.UUID, request: Request, admin: CampaignManager, db: DbSession
) -> None:
    campaign = await _get(db, campaign_id)
    if campaign.status == CampaignStatus.RUNNING:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pause the campaign before deleting it")
    await audit.record(
        db,
        actor=admin,
        action="campaign.delete",
        entity_type="campaign",
        entity_id=campaign.id,
        summary=f"Deleted campaign '{campaign.name}' (its calls stay in history)",
        ip_address=client_ip(request),
    )
    await db.delete(campaign)
    await db.commit()


async def _sheet_csv(url: str) -> str:
    found = re.search(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)", url)
    if not found:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Paste a Google Sheets link")
    gid = re.search(r"[#&?]gid=(\d+)", url)
    export = f"https://docs.google.com/spreadsheets/d/{found.group(1)}/export"
    params = {"format": "csv", **({"gid": gid.group(1)} if gid else {})}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(export, params=params)
    if response.status_code != 200 or "text/csv" not in response.headers.get("content-type", ""):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Couldn't read the sheet. Share it as 'Anyone with the link can view', or upload "
            "a CSV instead.",
        )
    if len(response.content) > MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The sheet is too large (5 MB max)")
    return response.text


@router.post("/{campaign_id}/contacts")
async def import_contacts(
    campaign_id: uuid.UUID, body: ImportIn, request: Request, admin: CampaignManager, db: DbSession
) -> dict[str, Any]:
    """Add contacts from a CSV (first row = column titles). Every other column becomes a
    customer detail, e.g. 'Customer Name' -> {{customer_name}}. Duplicates are skipped."""
    campaign = await _get(db, campaign_id)
    text = body.csv if body.csv else await _sheet_csv(body.sheet_url or "")
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if len(rows) < 2:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Put column titles in the first row and one contact per row below",
        )
    headers = [_snake(h) for h in rows[0]]
    wanted = _snake(body.phone_column) if body.phone_column else None
    phone_col = next(
        (i for i, h in enumerate(headers) if h == wanted or (not wanted and h in PHONE_HEADERS)),
        None,
    )
    if phone_col is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"No phone column found. Name it 'Phone' (columns: {', '.join(rows[0])})",
        )
    existing = set(
        await db.scalars(
            select(CampaignContact.phone).where(CampaignContact.campaign_id == campaign.id)
        )
    )
    if len(existing) + len(rows) - 1 > MAX_CONTACTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"At most {MAX_CONTACTS} contacts")
    added, duplicates, invalid = 0, 0, []
    for line, row in enumerate(rows[1:], start=2):
        raw = row[phone_col].strip() if phone_col < len(row) else ""
        try:
            phone = normalise_e164(raw)
        except ValueError:
            invalid.append({"row": line, "value": raw, "reason": "Not a phone number"})
            continue
        if phone in existing:
            duplicates += 1
            continue
        existing.add(phone)
        variables = {
            h: row[i].strip()
            for i, h in enumerate(headers)
            if h and i != phone_col and i < len(row) and row[i].strip()
        }
        db.add(CampaignContact(campaign_id=campaign.id, phone=phone, variables=variables))
        added += 1
    if campaign.status == CampaignStatus.COMPLETED and added:
        campaign.status = CampaignStatus.PAUSED  # new people to call; start it again
    await audit.record(
        db,
        actor=admin,
        action="campaign.contacts",
        entity_type="campaign",
        entity_id=campaign.id,
        summary=f"Added {added} contacts to '{campaign.name}'",
        ip_address=client_ip(request),
    )
    await db.commit()
    return {
        "added": added,
        "duplicates": duplicates,
        "invalid": invalid[:20],
        "invalid_count": len(invalid),
        "columns": [h for i, h in enumerate(headers) if h and i != phone_col],
    }


async def _contact_rows(
    db: DbSession,
    campaign_id: uuid.UUID,
    contact_status: ContactStatus | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[ContactOut]:
    query = (
        select(CampaignContact, Call)
        .outerjoin(Call, Call.id == CampaignContact.last_call_id)
        .where(CampaignContact.campaign_id == campaign_id)
        .order_by(CampaignContact.created_at, CampaignContact.phone)
        .offset(offset)
    )
    if contact_status:
        query = query.where(CampaignContact.status == contact_status)
    if limit:
        query = query.limit(limit)
    return [
        ContactOut(
            id=contact.id,
            phone=contact.phone,
            variables=contact.variables,
            status=contact.status,
            attempts=contact.attempts,
            last_result=contact.last_result,
            next_attempt_at=contact.next_attempt_at,
            call_id=call.id if call else None,
            outcome=call.outcome if call else None,
            summary=call.summary if call else None,
            extracted=(call.extracted or {}) if call else {},
        )
        for contact, call in (await db.execute(query)).all()
    ]


@router.get("/{campaign_id}/contacts")
async def list_contacts(
    campaign_id: uuid.UUID,
    _: CampaignViewer,
    db: DbSession,
    contact_status: Annotated[ContactStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    await _get(db, campaign_id)
    total_query = (
        select(func.count())
        .select_from(CampaignContact)
        .where(CampaignContact.campaign_id == campaign_id)
    )
    if contact_status:
        total_query = total_query.where(CampaignContact.status == contact_status)
    return {
        "items": await _contact_rows(db, campaign_id, contact_status, limit, offset),
        "total": await db.scalar(total_query) or 0,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{campaign_id}/export.csv")
async def export_results(campaign_id: uuid.UUID, _: CampaignManager, db: DbSession) -> Response:
    """Every contact with its result: their details, status, outcome, summary and details."""
    campaign = await _get(db, campaign_id)
    contacts = await _contact_rows(db, campaign_id)
    detail_names = list(dict.fromkeys(k for c in contacts for k in c.extracted))
    variable_names = list(dict.fromkeys(k for c in contacts for k in c.variables))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        [
            "phone",
            *variable_names,
            "status",
            "attempts",
            "outcome",
            "not_reached_reason",
            "summary",
            *detail_names,
        ]
    )
    for c in contacts:
        writer.writerow(
            [
                c.phone,
                *(c.variables.get(v, "") for v in variable_names),
                c.status.value,
                c.attempts,
                c.outcome or "",
                c.last_result or "",
                c.summary or "",
                *(c.extracted.get(d, "") for d in detail_names),
            ]
        )
    filename = re.sub(r"[^A-Za-z0-9_-]+", "-", campaign.name).strip("-") or "campaign"
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}-results.csv"'},
    )
