"""Webhooks and media WebSockets called by telephony providers (not by the dashboard).

Every URL carries the provider account's secret, so only that provider can reach it, and each
media WebSocket needs a single-use token issued when the call was answered.
"""

import asyncio
import contextlib
import json
import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from ymva.api.deps import DbSession
from ymva.db import get_sessionmaker
from ymva.logging import get_logger
from ymva.models import (
    Agent,
    Call,
    CallDirection,
    CallStatus,
    PhoneNumber,
    ProviderAccount,
    TelephonyProvider,
)
from ymva.services.calls import (
    CallSetupError,
    check_capacity,
    issue_stream_token,
    redeem_stream_token,
    runnable_version,
)
from ymva.services.integrations import (
    load_ai_keys,
    telephony_client,
    webhook_urls,
    websocket_url,
)
from ymva.telephony.base import digits, reject_xml, stream_xml
from ymva.voice import greeting
from ymva.voice.runtime import fail_call, load_setup, run_on_websocket
from ymva.voice.session import PIPELINE_SAMPLE_RATE, MissingCredentialError

router = APIRouter(prefix="/telephony", tags=["telephony"])
log = get_logger(__name__)

NOT_CONNECTED = (CallStatus.QUEUED, CallStatus.RINGING)


def _xml(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


async def _account(
    db: DbSession, provider: TelephonyProvider, account_id: uuid.UUID, secret: str
) -> ProviderAccount:
    account = await db.get(ProviderAccount, account_id)
    if (
        account is None
        or account.provider != provider
        or not secrets.compare_digest(account.webhook_secret, secret)
    ):
        raise HTTPException(404)
    return account


async def _form(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    form = await request.form()
    merged = {k: str(v) for k, v in request.query_params.items()}
    merged.update({k: str(v) for k, v in form.items()})
    return merged


@router.api_route("/{provider}/{account_id}/{secret}/answer", methods=["GET", "POST"])
async def answer(
    provider: TelephonyProvider, account_id: uuid.UUID, secret: str, request: Request, db: DbSession
) -> Response:
    account = await _account(db, provider, account_id, secret)
    hook = telephony_client(account).parse_webhook(await _form(request))
    call_param = request.query_params.get("call")

    if call_param:  # outbound call we placed
        call = await db.get(Call, uuid.UUID(call_param))
        if call is None or call.provider_account_id != account.id:
            return _xml(reject_xml("Sorry, this call could not be connected."))
    else:  # inbound call to one of our numbers
        to = digits(hook.to_number or "")
        number = await db.scalar(
            select(PhoneNumber)
            .where(PhoneNumber.provider_account_id == account.id, PhoneNumber.is_active)
            .where(PhoneNumber.e164 == f"+{to}")
        )
        agent = (
            await db.get(Agent, number.inbound_agent_id)
            if number and number.inbound_agent_id
            else None
        )
        if number is None or agent is None:
            log.warning("inbound call to a number without an agent", to=hook.to_number)
            return _xml(
                reject_xml("Sorry, this number is not available right now. Please call later.")
            )
        try:
            version, inbound_config = await runnable_version(db, agent)
            await check_capacity(db)
        except CallSetupError as exc:
            log.warning("inbound call rejected", reason=str(exc))
            return _xml(
                reject_xml("Sorry, all our lines are busy. Please call again in a little while.")
            )
        call = Call(
            agent_id=agent.id,
            agent_name=agent.name,
            agent_version_id=version.id,
            direction=CallDirection.INBOUND,
            status=CallStatus.RINGING,
            provider_account_id=account.id,
            phone_number_id=number.id,
            from_number=hook.from_number,
            to_number=number.e164,
            variables={"caller_number": hook.from_number or ""},
        )
        db.add(call)
        await db.flush()
        keys = await load_ai_keys(db)
        greeting.prefetch(call.id, inbound_config, call.variables, keys, PIPELINE_SAMPLE_RATE)

    if hook.provider_call_id:
        call.provider_call_id = hook.provider_call_id
    call.answered_at = datetime.now(UTC)
    await db.commit()

    token = await issue_stream_token(call.id)
    status_url = f"{webhook_urls(account)['status_url']}?call={call.id}"
    return _xml(stream_xml(websocket_url(provider.value, token), status_url))


@router.api_route("/{provider}/{account_id}/{secret}/status", methods=["GET", "POST"])
async def call_status(
    provider: TelephonyProvider, account_id: uuid.UUID, secret: str, request: Request, db: DbSession
) -> dict[str, str]:
    account = await _account(db, provider, account_id, secret)
    hook = telephony_client(account).parse_webhook(await _form(request))
    call_param = request.query_params.get("call")
    call = None
    if call_param:
        call = await db.get(Call, uuid.UUID(call_param))
    elif hook.provider_call_id:
        call = await db.scalar(
            select(Call).where(
                Call.provider_account_id == account.id,
                Call.provider_call_id == hook.provider_call_id,
            )
        )
    if call is None or call.provider_account_id != account.id:
        return {"status": "ignored"}
    # Only settle calls the voice session never handled (no answer, busy, failed...).
    if (
        call.status in NOT_CONNECTED
        and hook.status
        and hook.status not in ("ringing", "in_progress")
    ):
        call.status = CallStatus(hook.status)
        call.ended_at = datetime.now(UTC)
        call.end_reason = (hook.hangup_cause or hook.status or "")[:64]
    if (
        call.duration_seconds is None
        and hook.duration_seconds is not None
        and call.status != CallStatus.IN_PROGRESS
    ):
        call.duration_seconds = hook.duration_seconds
    await db.commit()
    return {"status": "ok"}


@router.websocket("/{provider}/ws/{token}")
async def media_stream(websocket: WebSocket, provider: TelephonyProvider, token: str) -> None:
    call_id = await redeem_stream_token(token)
    if call_id is None:
        await websocket.close(code=1008)
        return
    async with get_sessionmaker()() as db:
        call = await db.get(Call, call_id)
        account = (
            await db.get(ProviderAccount, call.provider_account_id)
            if call and call.provider_account_id
            else None
        )
    if call is None or account is None or account.provider != provider:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    client = telephony_client(account)
    try:
        start = None
        async with asyncio.timeout(10):
            while start is None:
                message = json.loads(await websocket.receive_text())
                start = client.parse_stream_start(message)
    except (TimeoutError, WebSocketDisconnect, ValueError) as exc:
        log.warning("media stream never started", call_id=str(call_id), error=repr(exc))
        await fail_call(call_id, "The provider's audio stream did not start")
        return

    try:
        setup = await load_setup(call_id)
    except (CallSetupError, MissingCredentialError) as exc:
        log.error("call setup failed", call_id=str(call_id), error=str(exc))
        await fail_call(call_id, str(exc))
        if start.provider_call_id:
            try:
                await client.hang_up(start.provider_call_id)
            except Exception:
                log.exception("hang-up after failed setup also failed")
        return
    setup.provider_call_id = setup.provider_call_id or start.provider_call_id
    with contextlib.suppress(WebSocketDisconnect):
        await run_on_websocket(websocket, setup, client.serializer(start))
