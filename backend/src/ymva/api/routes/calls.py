import contextlib
import uuid
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from starlette.websockets import WebSocketDisconnect

from ymva.api.deps import (
    SESSION_COOKIE,
    AdminUser,
    CurrentUser,
    DbSession,
    _user_from_session,
    client_ip,
)
from ymva.api.routes.numbers import normalise_e164
from ymva.api.schemas import Page
from ymva.config import get_settings
from ymva.db import get_sessionmaker
from ymva.logging import get_logger
from ymva.models import (
    Agent,
    AgentVersion,
    Call,
    CallDirection,
    CallEvent,
    CallEventType,
    CallStatus,
    PhoneNumber,
    ProviderAccount,
    Role,
)
from ymva.services import audit
from ymva.services.calls import (
    ACTIVE_SESSIONS,
    CallSetupError,
    check_capacity,
    runnable_version,
    string_variables,
)
from ymva.services.integrations import load_ai_keys, telephony_client, webhook_urls
from ymva.telephony import TelephonyError
from ymva.voice import greeting
from ymva.voice.browser import BROWSER_OUTPUT_SAMPLE_RATE, BrowserAudioSerializer
from ymva.voice.recording import recording_path
from ymva.voice.runtime import fail_call, load_setup, run_on_websocket
from ymva.voice.session import PIPELINE_SAMPLE_RATE as PHONE_SAMPLE_RATE
from ymva.voice.session import MissingCredentialError

router = APIRouter(prefix="/calls", tags=["calls"])
log = get_logger(__name__)


class CallOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    direction: CallDirection
    status: CallStatus
    agent_id: uuid.UUID | None
    agent_name: str | None
    from_number: str | None
    to_number: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    end_reason: str | None
    outcome: str | None
    languages: list[str]


class CallEventOut(BaseModel):
    seq: int
    at: datetime
    type: CallEventType
    language: str | None
    content: str
    data: dict[str, Any]


class CallDetail(CallOut):
    provider: str | None
    has_recording: bool
    variables: dict[str, Any]
    summary: str | None
    extracted: dict[str, Any]
    latency: dict[str, Any]
    cost_breakdown: dict[str, Any]
    error: str | None
    events: list[CallEventOut]


class OutboundIn(BaseModel):
    agent_id: uuid.UUID
    from_number_id: uuid.UUID
    to: str
    variables: dict[str, str] = Field(default_factory=dict)

    _normalise = field_validator("to")(normalise_e164)


def _call_out(call: Call, agent_names: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "id": call.id,
        "created_at": call.created_at,
        "direction": call.direction,
        "status": call.status,
        "agent_id": call.agent_id,
        "agent_name": agent_names.get(call.agent_id) if call.agent_id else None,
        "from_number": call.from_number,
        "to_number": call.to_number,
        "started_at": call.started_at,
        "ended_at": call.ended_at,
        "duration_seconds": call.duration_seconds,
        "end_reason": call.end_reason,
        "outcome": call.outcome,
        "languages": call.languages or [],
    }


@router.get("", response_model=Page[CallOut])
async def list_calls(
    _: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    agent_id: uuid.UUID | None = None,
    direction: CallDirection | None = None,
    call_status: Annotated[CallStatus | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    query = select(Call)
    if agent_id:
        query = query.where(Call.agent_id == agent_id)
    if direction:
        query = query.where(Call.direction == direction)
    if call_status:
        query = query.where(Call.status == call_status)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    calls = list(
        await db.scalars(query.order_by(Call.created_at.desc()).limit(limit).offset(offset))
    )
    names = {
        a.id: a.name
        for a in await db.scalars(
            select(Agent).where(Agent.id.in_({c.agent_id for c in calls if c.agent_id}))
        )
    }
    return {
        "items": [CallOut(**_call_out(c, names)) for c in calls],
        "total": total or 0,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(call_id: uuid.UUID, _: CurrentUser, db: DbSession) -> CallDetail:
    call = await db.get(Call, call_id)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    agent = await db.get(Agent, call.agent_id) if call.agent_id else None
    account = (
        await db.get(ProviderAccount, call.provider_account_id)
        if call.provider_account_id
        else None
    )
    events = await db.scalars(
        select(CallEvent).where(CallEvent.call_id == call.id).order_by(CallEvent.seq)
    )
    return CallDetail(
        **_call_out(call, {agent.id: agent.name} if agent else {}),
        provider=account.provider.value if account else None,
        has_recording=bool(call.recording_key),
        variables=call.variables or {},
        summary=call.summary,
        extracted=call.extracted or {},
        latency=call.latency or {},
        cost_breakdown=call.cost_breakdown or {},
        error=call.error,
        events=[
            CallEventOut(
                seq=e.seq, at=e.at, type=e.type, language=e.language, content=e.content, data=e.data
            )
            for e in events
        ],
    )


@router.get("/{call_id}/recording")
async def get_recording(call_id: uuid.UUID, _: CurrentUser, db: DbSession) -> FileResponse:
    call = await db.get(Call, call_id)
    if call is None or not call.recording_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No recording for this call")
    try:
        path = recording_path(call.recording_key)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No recording for this call") from exc
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The recording file is missing")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"call-{call.created_at:%Y%m%d-%H%M}-{str(call.id)[:8]}.wav",
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/outbound", response_model=CallOut, status_code=status.HTTP_201_CREATED)
async def place_outbound(
    body: OutboundIn, request: Request, admin: AdminUser, db: DbSession
) -> CallOut:
    agent = await db.get(Agent, body.agent_id)
    number = await db.get(PhoneNumber, body.from_number_id)
    if agent is None or number is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent or number not found")
    account = await db.get(ProviderAccount, number.provider_account_id)
    if account is None or not account.is_active or not number.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This number or its account is turned off")
    try:
        version, config = await runnable_version(db, agent)
        await check_capacity(db)
    except CallSetupError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    call = Call(
        agent_id=agent.id,
        agent_version_id=version.id,
        direction=CallDirection.OUTBOUND,
        status=CallStatus.QUEUED,
        provider_account_id=account.id,
        phone_number_id=number.id,
        from_number=number.e164,
        to_number=body.to,
        variables=string_variables(body.variables),
    )
    db.add(call)
    await db.flush()
    urls = webhook_urls(account)
    try:
        placed = await telephony_client(account).place_call(
            from_number=number.e164,
            to_number=body.to,
            answer_url=f"{urls['answer_url']}?call={call.id}",
            status_url=f"{urls['status_url']}?call={call.id}",
            time_limit=config.limits.max_duration_seconds + 30,
        )
    except TelephonyError as exc:
        call.status, call.error, call.end_reason = CallStatus.FAILED, str(exc), "provider_rejected"
        await db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    call.status = CallStatus.RINGING
    # Provider's request ID until the answer webhook reports the real call ID.
    call.provider_call_id = placed.provider_call_id
    # Prepare the greeting audio while the phone rings, so it plays the moment they answer.
    keys = await load_ai_keys(db)
    greeting.prefetch(call.id, config, call.variables, keys.get("sarvam"), PHONE_SAMPLE_RATE)
    await audit.record(
        db,
        actor=admin,
        action="call.outbound",
        entity_type="call",
        entity_id=call.id,
        summary=f"Placed a call from {number.e164} with '{agent.name}'",
        ip_address=client_ip(request),
    )
    await db.commit()
    return CallOut(**_call_out(call, {agent.id: agent.name}))


@router.post("/{call_id}/hangup", status_code=status.HTTP_202_ACCEPTED)
async def hang_up(
    call_id: uuid.UUID, request: Request, admin: AdminUser, db: DbSession
) -> dict[str, str]:
    call = await db.get(Call, call_id)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    session = ACTIVE_SESSIONS.get(call.id)
    if session is not None and session.task is not None:
        session._ended_by = "ended_from_dashboard"
        await session.task.cancel(reason="ended from dashboard")
    elif call.provider_account_id and call.provider_call_id:
        account = await db.get(ProviderAccount, call.provider_account_id)
        if account:
            try:
                await telephony_client(account).hang_up(call.provider_call_id)
            except TelephonyError as exc:
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    await audit.record(
        db,
        actor=admin,
        action="call.hangup",
        entity_type="call",
        entity_id=call.id,
        summary="Ended a call from the dashboard",
        ip_address=client_ip(request),
    )
    await db.commit()
    return {"status": "ending"}


def _origin_ok(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return False
    allowed = {urlsplit(get_settings().public_url).netloc, websocket.headers.get("host", "")}
    return urlsplit(origin).netloc in allowed


@router.websocket("/web")
async def browser_call(
    websocket: WebSocket, agent_id: uuid.UUID, version_id: uuid.UUID | None = None
) -> None:
    """Talk to an agent from the dashboard: its current draft, or a chosen saved version."""
    if not _origin_ok(websocket):
        await websocket.close(code=1008)
        return
    async with get_sessionmaker()() as db:
        token = websocket.cookies.get(SESSION_COOKIE)
        user = await _user_from_session(db, token) if token else None
        if user is None or user.role != Role.ADMIN:
            await websocket.close(code=1008)
            return
        agent = await db.get(Agent, agent_id)
        await websocket.accept()
        try:
            if agent is None:
                raise CallSetupError("Agent not found")
            version, _ = await runnable_version(db, agent, draft=True)
            config_used = agent.draft_config or version.config
            if version_id is not None:
                chosen = await db.get(AgentVersion, version_id)
                if chosen is None or chosen.agent_id != agent.id:
                    raise CallSetupError("That version doesn't belong to this agent")
                version, config_used = chosen, chosen.config
            await check_capacity(db)
        except CallSetupError as exc:
            await websocket.send_json({"type": "error", "content": str(exc)})
            await websocket.close()
            return
        call = Call(
            agent_id=agent.id,
            agent_version_id=version.id,
            direction=CallDirection.WEB,
            status=CallStatus.IN_PROGRESS,
            from_number=f"browser:{user.email}",
            agent_config=config_used,
            # Test values for the agent's variables arrive as ?var_customer_name=...
            variables={k[4:]: v for k, v in websocket.query_params.items() if k.startswith("var_")},
        )
        db.add(call)
        await db.flush()
        await audit.record(
            db,
            actor=user,
            action="call.browser_test",
            entity_type="call",
            entity_id=call.id,
            summary=f"Started a browser test call with '{agent.name}' (v{version.version})",
        )
        await db.commit()
        call_id = call.id

    await websocket.send_json(
        {
            "type": "call",
            "call_id": str(call_id),
            "version": None if version_id is None and agent.draft_config else version.version,
        }
    )
    try:
        setup = await load_setup(
            call_id, live_events=True, output_sample_rate=BROWSER_OUTPUT_SAMPLE_RATE
        )
    except (CallSetupError, MissingCredentialError) as exc:
        await fail_call(call_id, str(exc))
        await websocket.send_json({"type": "error", "content": str(exc)})
        await websocket.close()
        return
    with contextlib.suppress(WebSocketDisconnect):
        await run_on_websocket(websocket, setup, BrowserAudioSerializer())
