"""Placing an outbound phone call: used by "Call a phone" and by the campaign dialer."""

import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.models import (
    Agent,
    AgentStatus,
    Call,
    CallDirection,
    CallStatus,
    DoNotCall,
    PhoneNumber,
    ProviderAccount,
    TelephonyProvider,
    User,
)
from ymva.models.user import acting_api_key
from ymva.services.calls import (
    CallSetupError,
    DoNotCallError,
    check_capacity,
    runnable_version,
    string_variables,
)
from ymva.services.drain import is_draining
from ymva.services.integrations import (
    load_ai_keys,
    public_base_url,
    telephony_client,
    webhook_urls,
)
from ymva.telephony import TelephonyError
from ymva.voice import greeting
from ymva.voice.config import AgentConfig

PHONE_SAMPLE_RATE = 16000


# When the public address last answered; checked at most once a minute (campaigns dial a lot).
_reachable_at = 0.0


async def _check_public_address(provider: str) -> None:
    """The provider must reach our webhooks. A temporary tunnel can expire unnoticed, and the
    provider then only says "answer_url is not valid", so check it ourselves first."""
    global _reachable_at
    base = public_base_url()
    if urlsplit(base).hostname in ("localhost", "127.0.0.1"):
        raise CallSetupError(
            f"{provider} can't reach this computer's address (localhost). "
            "Restart with ./ymva start --tunnel, or set a public domain."
        )
    if time.monotonic() - _reachable_at < 60:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            ok = (await client.get(f"{base}/api/health")).status_code == 200
    except httpx.HTTPError:
        ok = False
    if not ok:
        raise CallSetupError(
            f"{provider} can't reach YMVA: the public address {base} isn't answering. A new "
            "tunnel needs a minute to come online; an old one expires after a while. Try again "
            "shortly, or run ./ymva start --tunnel for a new address (a domain is permanent)."
        )
    _reachable_at = time.monotonic()


async def start_outbound_call(
    db: AsyncSession,
    agent: Agent,
    number: PhoneNumber,
    to: str,
    variables: dict[str, Any],
    *,
    use_edits: bool = False,
    placed_by: User | None = None,
    callback_url: str | None = None,
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
    if account.provider != TelephonyProvider.SIMULATOR:
        await _check_public_address(account.provider.value.title())
    if agent.status in (AgentStatus.PAUSED, AgentStatus.ARCHIVED):  # a real phone rings
        raise CallSetupError(f"{agent.name} is {agent.status.value}. Resume it to make calls.")
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
        placed_by_id=placed_by.id if placed_by else None,
        placed_by_api_key_id=acting_api_key(placed_by) if placed_by else None,
        callback_url=callback_url,
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
