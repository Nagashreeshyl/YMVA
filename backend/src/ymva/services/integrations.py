"""AI provider keys and telephony accounts: storage (encrypted) and live verification."""

import secrets
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.config import get_settings
from ymva.models import AICredential, AIProvider, ProviderAccount
from ymva.security import decrypt_json, encrypt_json
from ymva.telephony import TelephonyError, client_for
from ymva.telephony.base import TelephonyProviderClient

TIMEOUT = httpx.Timeout(20.0, connect=5.0)

AI_PROVIDER_INFO: dict[AIProvider, dict[str, str]] = {
    AIProvider.SARVAM: {
        "label": "Sarvam AI",
        "purpose": "Speech recognition and voices for Indian languages",
        "key_url": "https://dashboard.sarvam.ai",
    },
    AIProvider.GOOGLE: {
        "label": "Google Gemini",
        "purpose": "Conversation model, and Gemini Live speech-to-speech",
        "key_url": "https://aistudio.google.com/apikey",
    },
}


class VerificationError(Exception):
    pass


def key_hint(key: str) -> str:
    return f"…{key[-4:]}" if len(key) >= 8 else "…"


async def verify_ai_key(provider: AIProvider, key: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            if provider == AIProvider.GOOGLE:
                response = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"pageSize": 1},
                    headers={"x-goog-api-key": key},
                )
            else:
                response = await client.post(
                    "https://api.sarvam.ai/text-to-speech",
                    headers={"api-subscription-key": key},
                    json={
                        "text": "Hello",
                        "target_language_code": "en-IN",
                        "model": "bulbul:v3",
                        "speaker": "priya",
                    },
                )
    except httpx.HTTPError as exc:
        raise VerificationError(
            f"Couldn't reach {provider.value}: {exc.__class__.__name__}"
        ) from exc
    if response.status_code in (401, 403):
        raise VerificationError("The key was rejected. Check it was copied completely.")
    if response.status_code == 429:
        raise VerificationError("The provider is rate-limiting this key; try again shortly.")
    if response.status_code >= 300:
        raise VerificationError(f"Check failed ({response.status_code}): {response.text[:200]}")


async def save_ai_key(
    session: AsyncSession, provider: AIProvider, key: str, user_id: Any
) -> AICredential:
    credential = await session.scalar(select(AICredential).where(AICredential.provider == provider))
    if credential is None:
        credential = AICredential(provider=provider, encrypted_key="", key_hint="")
        session.add(credential)
    credential.encrypted_key = encrypt_json({"api_key": key})
    credential.key_hint = key_hint(key)
    credential.updated_by_id = user_id
    try:
        await verify_ai_key(provider, key)
        credential.verified_at, credential.last_error = datetime.now(UTC), None
    except VerificationError as exc:
        credential.verified_at, credential.last_error = None, str(exc)
    return credential


async def reverify_ai_key(credential: AICredential) -> None:
    key = decrypt_json(credential.encrypted_key)["api_key"]
    try:
        await verify_ai_key(credential.provider, key)
        credential.verified_at, credential.last_error = datetime.now(UTC), None
    except VerificationError as exc:
        credential.verified_at, credential.last_error = None, str(exc)


async def load_ai_keys(session: AsyncSession) -> dict[str, str]:
    rows = await session.scalars(select(AICredential))
    return {row.provider.value: decrypt_json(row.encrypted_key)["api_key"] for row in rows}


# ---- telephony ---------------------------------------------------------------------------


def new_webhook_secret() -> str:
    return secrets.token_urlsafe(24)


def telephony_client(account: ProviderAccount) -> TelephonyProviderClient:
    return client_for(account.provider, decrypt_json(account.encrypted_credentials))


async def verify_account(account: ProviderAccount) -> None:
    try:
        await telephony_client(account).verify()
        account.verified_at, account.last_error = datetime.now(UTC), None
    except TelephonyError as exc:
        account.verified_at, account.last_error = None, str(exc)


def public_base_url() -> str:
    settings = get_settings()
    return (settings.telephony_public_url or settings.public_url).rstrip("/")


def webhook_urls(account: ProviderAccount) -> dict[str, str]:
    provider = account.provider.value
    base = f"{public_base_url()}/api/telephony/{provider}/{account.id}/{account.webhook_secret}"
    return {"answer_url": f"{base}/answer", "status_url": f"{base}/status"}


def websocket_url(provider: str, token: str) -> str:
    base = public_base_url()
    scheme = "wss" if base.startswith("https://") else "ws"
    host = base.split("://", 1)[1]
    return f"{scheme}://{host}/api/telephony/{provider}/ws/{token}"
