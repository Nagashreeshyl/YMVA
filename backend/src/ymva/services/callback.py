"""Tells the app that started a call (n8n, a CRM) how it went: a POST to the call's
callback_url once the call has ended and been analysed, or once it ended unanswered."""

import asyncio
import uuid
from typing import Any

import httpx

from ymva.db import get_sessionmaker
from ymva.logging import get_logger
from ymva.models import Call, call_ref
from ymva.services.egress import EVENT_HOOKS, guarded_transport

log = get_logger(__name__)
_running: set[asyncio.Task[None]] = set()


def payload(call: Call) -> dict[str, Any]:
    from ymva.services import retries  # retries imports this module

    retrying = retries.will_retry(call)
    connected = retries.connected_at(call)
    return {
        "event": "call.ended",
        "call_id": str(call.id),
        "call_number": call.number,
        "call_ref": call_ref(call.number),
        "status": call.status.value,
        "outcome": call.outcome,
        "summary": retries.summary(call),
        "action_items": call.action_items or [],
        "details": call.extracted or {},
        "branch": call.branch,
        # Attempt 1 is the first call; retries share its first_call_id. When will_retry is
        # true another attempt follows (about retry_in_minutes later) with its own callback.
        "attempt": call.attempt,
        "attempts": call.attempt,  # calls made to this customer so far, this one included
        # When the customer was reached (this call's start); null when they weren't.
        "connected_at": connected.isoformat() if connected else None,
        "first_call_id": str(call.first_call_id or call.id),
        "will_retry": retrying,
        "retry_in_minutes": retries.retry_setting(call).wait_minutes if retrying else None,
        "agent_id": str(call.agent_id) if call.agent_id else None,
        "agent_name": call.agent_name,
        "to": call.to_number,
        "from": call.from_number,
        "duration_seconds": call.duration_seconds,
        "end_reason": call.end_reason,
        "variables": call.variables or {},
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
    }


async def send_callback(call_id: uuid.UUID) -> None:
    async with get_sessionmaker()() as db:
        call = await db.get(Call, call_id)
        if call is None or not call.callback_url:
            return
        url, body = call.callback_url, payload(call)
    async with httpx.AsyncClient(
        transport=guarded_transport(), event_hooks=EVENT_HOOKS, timeout=10
    ) as client:
        for attempt in range(3):
            try:
                resp = await client.post(url, json=body)
                if resp.status_code < 500:
                    log.info("callback sent", call_id=str(call_id), status=resp.status_code)
                    return
            except httpx.HTTPError as exc:
                log.warning("callback failed", call_id=str(call_id), error=str(exc))
            await asyncio.sleep(2**attempt * 5)
    log.warning("callback gave up", call_id=str(call_id))


def schedule(call_id: uuid.UUID) -> None:
    task = asyncio.create_task(send_callback(call_id))
    _running.add(task)
    task.add_done_callback(_running.discard)
