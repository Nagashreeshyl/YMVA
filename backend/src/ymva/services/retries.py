"""Retries of single calls (the agent's Retry setting): a number that wasn't reached (no
answer, busy, failed, voicemail, nobody spoke) is called again after wait_minutes, up to
max_retries more times. Runs in the dialer's loop, so only one API process places them.
Campaign calls are left to the campaign's own retry settings."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.logging import get_logger
from ymva.models import Agent, Call, CallDirection, CallStatus, PhoneNumber
from ymva.services.analysis import NO_CONVERSATION
from ymva.services.calls import CallSetupError, LinesBusyError
from ymva.services.outbound import start_outbound_call
from ymva.telephony import TelephonyError
from ymva.voice.config import AgentConfig, Retry
from ymva.voice.voicemail import VOICEMAIL

log = get_logger(__name__)

# Same limits as the campaign dialer: a lost "no answer" webhook, and the outcome written a few
# seconds after a connected call ends.
RING_TIMEOUT = timedelta(minutes=3)
OUTCOME_WAIT = timedelta(seconds=30)
NOT_REACHED = (CallStatus.NO_ANSWER, CallStatus.BUSY, CallStatus.FAILED)


def retry_setting(call: Call) -> Retry:
    try:
        return AgentConfig.model_validate(call.agent_config or {}).retry
    except ValueError:
        return Retry()


def reached(call: Call, now: datetime) -> bool | None:
    """Whether the customer was reached; None while that isn't known yet."""
    if call.status in (CallStatus.QUEUED, CallStatus.RINGING):
        return False if now - call.created_at > RING_TIMEOUT else None
    if call.status == CallStatus.IN_PROGRESS:
        return None
    if call.status in NOT_REACHED:
        return False
    if call.status == CallStatus.CANCELED:  # hung up on purpose by the app or an admin
        return True
    if call.end_reason == VOICEMAIL:
        return False
    if call.outcome is None:
        return None if now - (call.ended_at or call.created_at) < OUTCOME_WAIT else True
    return call.outcome != NO_CONVERSATION


def will_retry(call: Call, now: datetime | None = None) -> bool:
    """True when this call ended unreached and the agent has retries left for it."""
    setting = retry_setting(call)
    return (
        call.retry_status in ("pending", "scheduled")
        and setting.enabled
        and call.attempt <= setting.max_retries
        and reached(call, now or datetime.now(UTC)) is False
    )


async def _decide(db: AsyncSession, now: datetime) -> None:
    pending = await db.scalars(
        select(Call)
        .where(
            Call.retry_status == "pending",
            Call.direction == CallDirection.OUTBOUND,
            Call.created_at > now - timedelta(days=3),
        )
        .limit(200)
    )
    for call in pending:
        if reached(call, now) is None:
            continue
        if will_retry(call, now):
            call.retry_status = "scheduled"
            call.retry_at = now + timedelta(minutes=retry_setting(call).wait_minutes)
        else:
            call.retry_status = "none"
    await db.commit()


async def _place(db: AsyncSession, now: datetime) -> bool:
    """Place due retries. Returns False when the platform is out of lines."""
    due = list(
        await db.scalars(
            select(Call)
            .where(Call.retry_status == "scheduled", Call.retry_at <= now)
            .order_by(Call.retry_at)
            .limit(20)
            .with_for_update(skip_locked=True)
        )
    )
    for call in due:
        agent = await db.get(Agent, call.agent_id) if call.agent_id else None
        number = await db.get(PhoneNumber, call.phone_number_id) if call.phone_number_id else None
        if agent is None or number is None or not call.to_number:
            call.retry_status = "none"
            await db.commit()
            continue
        try:
            await start_outbound_call(
                db,
                agent,
                number,
                call.to_number,
                call.variables or {},
                callback_url=call.callback_url,
                retry_of=call,
            )
        except LinesBusyError:
            call.retry_at = now + timedelta(minutes=1)
            await db.commit()
            return False
        except CallSetupError as exc:  # agent paused, do-not-call, number turned off...
            log.info("retry not placed", call_id=str(call.id), reason=str(exc))
            call.retry_status = "none"
            call.error = f"Retry not placed: {exc}"[:500]
            await db.commit()
            continue
        except TelephonyError:
            pass  # saved as a failed attempt, which is itself checked for a retry
        call.retry_status = "placed"
        await db.commit()
    return True


async def tick(db: AsyncSession, now: datetime) -> bool:
    """One pass of the dialer loop. Returns False when out of lines."""
    await _decide(db, now)
    return await _place(db, now)
