"""Placing an outbound phone call: used by "Call a phone" and by the campaign dialer."""

from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.models import (
    Agent,
    Call,
    CallDirection,
    CallStatus,
    DoNotCall,
    PhoneNumber,
    ProviderAccount,
    TelephonyProvider,
)
from ymva.services.calls import (
    CallSetupError,
    DoNotCallError,
    check_capacity,
    runnable_version,
    string_variables,
)
from ymva.services.drain import is_draining
from ymva.services.integrations import load_ai_keys, telephony_client, webhook_urls
from ymva.telephony import TelephonyError
from ymva.voice import greeting
from ymva.voice.config import AgentConfig

PHONE_SAMPLE_RATE = 16000


async def start_outbound_call(
    db: AsyncSession,
    agent: Agent,
    number: PhoneNumber,
    to: str,
    variables: dict[str, Any],
    *,
    use_edits: bool = False,
) -> Call:
    """Create the call and ask the provider to dial. Raises CallSetupError when the call can't
    start (no published version, lines busy, number off) and TelephonyError when the provider
    refuses; in that case the call is saved as failed. The caller commits."""
    if await is_draining():
        raise CallSetupError("YMVA is restarting for an update. Try again in a few minutes.")
    if (entry := await db.get(DoNotCall, to)) and not entry.pending:
        raise DoNotCallError("This number is on the do-not-call list")
    account = await db.get(ProviderAccount, number.provider_account_id)
    if account is None or not account.is_active or not number.is_active:
        raise CallSetupError("This number or its account is turned off")
    host = urlsplit(webhook_urls(account)["answer_url"]).hostname or ""
    if account.provider != TelephonyProvider.SIMULATOR and host in ("localhost", "127.0.0.1"):
        raise CallSetupError(
            f"{account.provider.value.title()} can't reach this computer's address (localhost). "
            "Restart with ./ymva start --tunnel, or set a public domain."
        )
    version, config = await runnable_version(db, agent, draft=use_edits)
    if use_edits and agent.draft_config is not None:  # "Call a phone" testing unpublished edits
        try:
            config = AgentConfig.model_validate(agent.draft_config)
        except ValidationError as exc:
            raise CallSetupError(
                "Your current edits aren't complete yet. Fix them or call with the published "
                "version."
            ) from exc
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
        # The settings this call runs with, read again by the after-call results.
        agent_config=config.model_dump(mode="json"),
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
