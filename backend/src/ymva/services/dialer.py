"""Campaign dialer: every few seconds, settle finished calls and dial the next contacts.

Calls go out only within each campaign's calling hours, at most max_concurrent at a time per
campaign (and never past the platform-wide line limit). Unanswered, busy and failed calls are
retried after retry_after_minutes, up to max_attempts. A Redis lock makes sure only one API
process dials, so contacts are never called twice.
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.config import get_settings
from ymva.db import get_sessionmaker
from ymva.logging import get_logger
from ymva.models import (
    Agent,
    Call,
    CallStatus,
    Campaign,
    CampaignContact,
    CampaignStatus,
    ContactStatus,
    PhoneNumber,
)
from ymva.redis import get_redis
from ymva.services import retries
from ymva.services.analysis import NO_CONVERSATION
from ymva.services.calls import BranchError, CallSetupError, DoNotCallError, LinesBusyError
from ymva.services.drain import is_draining
from ymva.services.outbound import start_outbound_call
from ymva.telephony import TelephonyError

log = get_logger(__name__)

TICK_SECONDS = 5.0
LOCK_KEY = "dialer:lock"
DIALER_TICK_KEY = "dialer:last_tick"
# A call the provider never reported back on (lost webhook) counts as unanswered after this.
RING_TIMEOUT = timedelta(minutes=3)
# A connected call's outcome is written a few seconds after it ends; wait for it this long.
OUTCOME_WAIT = timedelta(seconds=30)
UNREACHED_RESULT = {
    CallStatus.NO_ANSWER: "No answer",
    CallStatus.BUSY: "Busy",
    CallStatus.FAILED: "Call failed",
    CallStatus.CANCELED: "Call cancelled",
}


def within_hours(campaign: Campaign, now: datetime) -> bool:
    local = now.astimezone(ZoneInfo(get_settings().timezone))
    return (
        local.weekday() in campaign.days
        and campaign.start_time <= local.strftime("%H:%M") < campaign.end_time
    )


def _not_reached(contact: CampaignContact, campaign: Campaign, result: str, now: datetime) -> None:
    contact.last_result = result
    if contact.attempts >= campaign.max_attempts:
        contact.status, contact.next_attempt_at = ContactStatus.FAILED, None
    else:
        contact.status = ContactStatus.RETRY
        contact.next_attempt_at = now + timedelta(minutes=campaign.retry_after_minutes)


def settle(contact: CampaignContact, call: Call | None, campaign: Campaign, now: datetime) -> None:
    """Move a contact on once its call has finished; leave it while the call is still live."""
    if call is None:  # the call record was removed
        _not_reached(contact, campaign, "Call record missing", now)
        return
    if call.status in (CallStatus.QUEUED, CallStatus.RINGING):
        if now - call.created_at > RING_TIMEOUT:
            _not_reached(contact, campaign, "No answer", now)
        return
    if call.status == CallStatus.IN_PROGRESS:
        return
    if call.status != CallStatus.COMPLETED:
        _not_reached(contact, campaign, UNREACHED_RESULT.get(call.status, call.status.value), now)
        return
    ended = call.ended_at or call.created_at
    if call.outcome is None and now - ended < OUTCOME_WAIT:
        return
    if call.outcome == NO_CONVERSATION:  # picked up but nobody spoke (voicemail, silence)
        _not_reached(contact, campaign, "Answered but nobody spoke", now)
        return
    contact.status, contact.next_attempt_at, contact.last_result = ContactStatus.DONE, None, None


async def _settle_calling(db: AsyncSession, now: datetime) -> None:
    rows = await db.execute(
        select(CampaignContact, Call, Campaign)
        .join(Campaign, Campaign.id == CampaignContact.campaign_id)
        .outerjoin(Call, Call.id == CampaignContact.last_call_id)
        .where(CampaignContact.status == ContactStatus.CALLING)
    )
    for contact, call, campaign in rows.all():
        settle(contact, call, campaign, now)
    await db.commit()


async def _dial(db: AsyncSession, campaign: Campaign, now: datetime) -> bool:
    """Place calls for one campaign. Returns False when the platform is out of lines."""
    calling = await db.scalar(
        select(func.count())
        .select_from(CampaignContact)
        .where(
            CampaignContact.campaign_id == campaign.id,
            CampaignContact.status == ContactStatus.CALLING,
        )
    )
    free = campaign.max_concurrent - (calling or 0)
    if free <= 0:
        return True
    agent = await db.get(Agent, campaign.agent_id) if campaign.agent_id else None
    numbers = [
        n
        for n in await db.scalars(
            select(PhoneNumber).where(
                PhoneNumber.id.in_([uuid.UUID(i) for i in campaign.phone_number_ids]),
                PhoneNumber.is_active,
            )
        )
    ]
    if agent is None or not numbers:
        campaign.last_error = "Choose an agent and at least one active number to call from"
        await db.commit()
        return True
    due = list(
        await db.scalars(
            select(CampaignContact)
            .where(
                CampaignContact.campaign_id == campaign.id,
                or_(
                    CampaignContact.status == ContactStatus.PENDING,
                    (CampaignContact.status == ContactStatus.RETRY)
                    & (CampaignContact.next_attempt_at <= now),
                ),
            )
            .order_by(CampaignContact.attempts, CampaignContact.created_at)
            .limit(free)
            .with_for_update(skip_locked=True)
        )
    )
    for contact in due:
        number = numbers[contact.attempts % len(numbers)]  # rotate caller IDs on retries
        try:
            call = await start_outbound_call(
                db, agent, number, contact.phone, contact.variables, retries=False
            )
        except LinesBusyError:
            return False  # out of lines: stop dialing everywhere this tick, try again next
        except DoNotCallError:
            contact.status, contact.last_result = ContactStatus.FAILED, "On the do-not-call list"
            await db.commit()
            continue
        except BranchError as exc:  # this contact's row lacks a valid branch; dial the rest
            contact.status, contact.last_result = ContactStatus.FAILED, str(exc)[:200]
            await db.commit()
            continue
        except CallSetupError as exc:
            campaign.last_error = str(exc)[:300]
            await db.commit()
            return True
        except TelephonyError as exc:
            contact.attempts += 1
            _not_reached(contact, campaign, f"Provider: {exc}"[:200], now)
            await db.commit()
            continue
        contact.status, contact.attempts = ContactStatus.CALLING, contact.attempts + 1
        contact.last_call_id, contact.next_attempt_at = call.id, None
        campaign.last_error = None
        await db.commit()
    return True


async def _finish_if_done(db: AsyncSession, campaign: Campaign, now: datetime) -> None:
    left = await db.scalar(
        select(func.count())
        .select_from(CampaignContact)
        .where(
            CampaignContact.campaign_id == campaign.id,
            CampaignContact.status.in_(
                [ContactStatus.PENDING, ContactStatus.RETRY, ContactStatus.CALLING]
            ),
        )
    )
    if not left:
        campaign.status, campaign.completed_at = CampaignStatus.COMPLETED, now
        await db.commit()


async def tick(now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    async with get_sessionmaker()() as db:
        await _settle_calling(db, now)
        if await is_draining():
            return  # an update is waiting for live calls to finish
        running = list(
            await db.scalars(select(Campaign).where(Campaign.status == CampaignStatus.RUNNING))
        )
        lines_free = await retries.tick(db, now)
        for campaign in running:
            await _finish_if_done(db, campaign, now)
            if campaign.status != CampaignStatus.RUNNING:
                continue
            if lines_free and within_hours(campaign, now):
                lines_free = await _dial(db, campaign, now)


async def run_forever() -> None:
    """Started with the API; only the process holding the lock dials."""
    redis = get_redis()
    while True:
        try:
            if await redis.set(LOCK_KEY, "1", nx=True, ex=int(TICK_SECONDS * 3)):
                try:
                    await tick()
                    await redis.set(DIALER_TICK_KEY, str(time.time()))  # for health checks
                finally:
                    await redis.delete(LOCK_KEY)
        except Exception:
            log.exception("dialer tick failed")
        await asyncio.sleep(TICK_SECONDS)
