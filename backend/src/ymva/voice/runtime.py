"""Starts a CallSession for a stored call and keeps it in the active-session registry."""

import uuid

from fastapi import WebSocket
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

from ymva.db import get_sessionmaker
from ymva.logging import get_logger
from ymva.models import Agent, AgentVersion, Call, CallStatus
from ymva.services.calls import ACTIVE_SESSIONS, CallSetupError, string_variables
from ymva.services.integrations import load_ai_keys
from ymva.voice.config import AgentConfig
from ymva.voice.session import CallSession, CallSetup, MissingCredentialError, required_providers

log = get_logger(__name__)


async def load_setup(call_id: uuid.UUID, *, live_events: bool = False) -> CallSetup:
    async with get_sessionmaker()() as db:
        call = await db.get(Call, call_id)
        if call is None or call.agent_version_id is None:
            raise CallSetupError("Call not found or has no agent")
        version = await db.get(AgentVersion, call.agent_version_id)
        agent = await db.get(Agent, call.agent_id) if call.agent_id else None
        if version is None or agent is None:
            raise CallSetupError("The agent for this call no longer exists")
        config = AgentConfig.model_validate(version.config)
        keys = await load_ai_keys(db)
    missing = sorted(required_providers(config) - keys.keys())
    if missing:
        raise MissingCredentialError(
            f"Missing API key for {', '.join(missing)}. Add it in Integrations."
        )
    return CallSetup(
        call_id=call_id,
        config=config,
        variables=string_variables(call.variables),
        keys=keys,
        send_live_events=live_events,
        provider_call_id=call.provider_call_id,
    )


async def fail_call(call_id: uuid.UUID, reason: str) -> None:
    async with get_sessionmaker()() as db:
        call = await db.get(Call, call_id)
        if call is not None and call.status in (
            CallStatus.QUEUED,
            CallStatus.RINGING,
            CallStatus.IN_PROGRESS,
        ):
            call.status = CallStatus.FAILED
            call.end_reason = "setup_failed"
            call.error = reason[:2000]
            await db.commit()


async def run_on_websocket(
    websocket: WebSocket, setup: CallSetup, serializer: FrameSerializer
) -> None:
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
            session_timeout=setup.config.limits.max_duration_seconds + 60,
        ),
    )
    session = CallSession(setup, transport)
    ACTIVE_SESSIONS[setup.call_id] = session
    try:
        await session.run()
    finally:
        ACTIVE_SESSIONS.pop(setup.call_id, None)
