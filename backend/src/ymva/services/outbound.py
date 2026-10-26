"""Placing an outbound phone call: used by "Call a phone" and by the campaign dialer."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ymva.models import Agent, Call, CallDirection, CallStatus, PhoneNumber, ProviderAccount
from ymva.services.calls import CallSetupError, check_capacity, runnable_version, string_variables
from ymva.services.integrations import load_ai_keys, telephony_client, webhook_urls
from ymva.telephony import TelephonyError
from ymva.voice import greeting

PHONE_SAMPLE_RATE = 16000


async def start_outbound_call(
    db: AsyncSession, agent: Agent, number: PhoneNumber, to: str, variables: dict[str, Any]
) -> Call:
    """Create the call and ask the provider to dial. Raises CallSetupError when the call can't
    start (no published version, lines busy, number off) and TelephonyError when the provider
    refuses; in that case the call is saved as failed. The caller commits."""
    account = await db.get(ProviderAccount, number.provider_account_id)
    if account is None or not account.is_active or not number.is_active:
        raise CallSetupError("This number or its account is turned off")
    version, config = await runnable_version(db, agent)
    await check_capacity(db)

    call = Call(
        agent_id=agent.id,
        agent_name=agent.name,
        agent_version_id=version.id,
        direction=CallDirection.OUTBOUND,
        status=CallStatus.QUEUED,
        provider_account_id=account.id,
        phone_number_id=number.id,
        from_number=number.e164,
        to_number=to,
        variables=string_variables(variables),
    )
    db.add(call)
    await db.flush()
    urls = webhook_urls(account)
    try:
        placed = await telephony_client(account).place_call(
            from_number=number.e164,
            to_number=to,
            answer_url=f"{urls['answer_url']}?call={call.id}",
            status_url=f"{urls['status_url']}?call={call.id}",
            time_limit=config.limits.max_duration_seconds + 30,
        )
    except TelephonyError as exc:
        call.status, call.error, call.end_reason = CallStatus.FAILED, str(exc), "provider_rejected"
        raise
    call.status = CallStatus.RINGING
    # Provider's request ID until the answer webhook reports the real call ID.
    call.provider_call_id = placed.provider_call_id
    # Prepare the greeting audio while the phone rings, so it plays the moment they answer.
    greeting.prefetch(call.id, config, call.variables, await load_ai_keys(db), PHONE_SAMPLE_RATE)
    return call
