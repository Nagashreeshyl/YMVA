import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ymva.api.deps import AdminUser, CurrentUser, DbSession, client_ip
from ymva.models import AICredential, AIProvider, PhoneNumber, ProviderAccount, TelephonyProvider
from ymva.security import decrypt_json, encrypt_json
from ymva.services import audit
from ymva.services.integrations import (
    AI_PROVIDER_INFO,
    new_webhook_secret,
    public_base_url,
    reverify_ai_key,
    save_ai_key,
    verify_account,
    webhook_urls,
)
from ymva.telephony import PROVIDERS, SUPPORTED

router = APIRouter(prefix="/integrations", tags=["integrations"])


class AIProviderOut(BaseModel):
    provider: AIProvider
    label: str
    purpose: str
    key_url: str
    configured: bool
    key_hint: str | None
    verified_at: datetime | None
    last_error: str | None


class TelephonyAccountOut(BaseModel):
    id: uuid.UUID
    provider: TelephonyProvider
    name: str
    credential_hint: str
    is_active: bool
    max_concurrent_calls: int
    # What the provider charges per minute of call, in rupees (for the cost figures).
    cost_per_minute_inr: float | None
    verified_at: datetime | None
    last_error: str | None
    answer_url: str
    status_url: str
    numbers: int


class CredentialFieldOut(BaseModel):
    key: str
    label: str
    secret: bool
    hint: str
    multiline: bool


class TelephonyProviderOut(BaseModel):
    id: TelephonyProvider
    name: str
    fields: list[CredentialFieldOut]
    setup: str


class IntegrationsOut(BaseModel):
    ai: list[AIProviderOut]
    telephony: list[TelephonyAccountOut]
    telephony_providers: list[TelephonyProviderOut]
    public_base_url: str
    public_url_is_https: bool


class AIKeyIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=500)


class TelephonyAccountIn(BaseModel):
    provider: TelephonyProvider
    name: str = Field(min_length=1, max_length=120)
    # The provider's own fields (see telephony_providers); auth_id/auth_token still accepted.
    credentials: dict[str, str] = Field(default_factory=dict)
    auth_id: str | None = Field(default=None, max_length=120)
    auth_token: str | None = Field(default=None, max_length=200)
    max_concurrent_calls: int = Field(default=10, ge=1, le=500)
    cost_per_minute_inr: float | None = Field(default=None, ge=0, le=100)

    def given(self) -> dict[str, str]:
        legacy = {"auth_id": self.auth_id, "auth_token": self.auth_token}
        return {k: v for k, v in legacy.items() if v} | self.credentials


class TelephonyAccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    # Only the fields being changed; empty ones keep their stored value.
    credentials: dict[str, str] = Field(default_factory=dict)
    auth_id: str | None = Field(default=None, max_length=120)
    auth_token: str | None = Field(default=None, max_length=200)
    max_concurrent_calls: int | None = Field(default=None, ge=1, le=500)
    cost_per_minute_inr: float | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None

    def given(self) -> dict[str, str]:
        legacy = {"auth_id": self.auth_id, "auth_token": self.auth_token}
        return {k: v for k, v in (legacy | self.credentials).items() if v}


def _ai_out(provider: AIProvider, credential: AICredential | None) -> AIProviderOut:
    info = AI_PROVIDER_INFO[provider]
    return AIProviderOut(
        provider=provider,
        configured=credential is not None,
        key_hint=credential.key_hint if credential else None,
        verified_at=credential.verified_at if credential else None,
        last_error=credential.last_error if credential else None,
        **info,
    )


def _account_out(account: ProviderAccount, numbers: int) -> TelephonyAccountOut:
    urls = webhook_urls(account)
    return TelephonyAccountOut(
        id=account.id,
        provider=account.provider,
        name=account.name,
        credential_hint=account.credential_hint,
        is_active=account.is_active,
        max_concurrent_calls=account.max_concurrent_calls,
        cost_per_minute_inr=account.settings.get("cost_per_minute_inr"),
        verified_at=account.verified_at,
        last_error=account.last_error,
        answer_url=urls["answer_url"],
        status_url=urls["status_url"],
        numbers=numbers,
    )


@router.get("", response_model=IntegrationsOut)
async def list_integrations(user: CurrentUser, db: DbSession) -> IntegrationsOut:
    credentials = {c.provider: c for c in await db.scalars(select(AICredential))}
    counts = dict(
        (
            await db.execute(
                select(PhoneNumber.provider_account_id, func.count()).group_by(
                    PhoneNumber.provider_account_id
                )
            )
        ).all()
    )
    accounts = await db.scalars(select(ProviderAccount).order_by(ProviderAccount.created_at))
    base = public_base_url()
    return IntegrationsOut(
        ai=[_ai_out(p, credentials.get(p)) for p in AIProvider],
        telephony=[_account_out(a, counts.get(a.id, 0)) for a in accounts],
        telephony_providers=[
            TelephonyProviderOut(
                id=p,
                name=PROVIDERS[p].name,
                fields=[CredentialFieldOut(**vars(f)) for f in PROVIDERS[p].fields],
                setup=PROVIDERS[p].setup,
            )
            for p in SUPPORTED
        ],
        public_base_url=base,
        public_url_is_https=base.startswith("https://"),
    )


@router.put("/ai/{provider}", response_model=AIProviderOut)
async def save_key(
    provider: AIProvider, body: AIKeyIn, request: Request, admin: AdminUser, db: DbSession
) -> AIProviderOut:
    credential = await save_ai_key(db, provider, body.api_key.strip(), admin.id)
    await audit.record(
        db,
        actor=admin,
        action="integration.ai_key_saved",
        entity_type="integration",
        entity_id=provider.value,
        summary=f"Saved {AI_PROVIDER_INFO[provider]['label']} API key ({credential.key_hint})",
        changes={"verified": credential.verified_at is not None},
        ip_address=client_ip(request),
    )
    await db.commit()
    return _ai_out(provider, credential)


@router.post("/ai/{provider}/test", response_model=AIProviderOut)
async def test_key(provider: AIProvider, _: AdminUser, db: DbSession) -> AIProviderOut:
    credential = await db.scalar(select(AICredential).where(AICredential.provider == provider))
    if credential is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No key saved for this provider")
    await reverify_ai_key(credential)
    await db.commit()
    return _ai_out(provider, credential)


@router.delete("/ai/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(
    provider: AIProvider, request: Request, admin: AdminUser, db: DbSession
) -> None:
    credential = await db.scalar(select(AICredential).where(AICredential.provider == provider))
    if credential is None:
        return
    await db.delete(credential)
    await audit.record(
        db,
        actor=admin,
        action="integration.ai_key_removed",
        entity_type="integration",
        entity_id=provider.value,
        summary=f"Removed {AI_PROVIDER_INFO[provider]['label']} API key",
        ip_address=client_ip(request),
    )
    await db.commit()


@router.post("/telephony", response_model=TelephonyAccountOut, status_code=status.HTTP_201_CREATED)
async def add_account(
    body: TelephonyAccountIn, request: Request, admin: AdminUser, db: DbSession
) -> TelephonyAccountOut:
    cls = PROVIDERS.get(body.provider)
    if cls is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"{body.provider.value} isn't supported yet"
        )
    try:
        credentials = cls.validate_credentials(body.given())
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    account = ProviderAccount(
        provider=body.provider,
        name=body.name.strip(),
        encrypted_credentials=encrypt_json(credentials),
        credential_hint=cls.credential_hint(credentials),
        webhook_secret=new_webhook_secret(),
        max_concurrent_calls=body.max_concurrent_calls,
        settings={"cost_per_minute_inr": body.cost_per_minute_inr},
    )
    db.add(account)
    await db.flush()
    await verify_account(account)
    await audit.record(
        db,
        actor=admin,
        action="integration.telephony_added",
        entity_type="provider_account",
        entity_id=account.id,
        summary=f"Connected {body.provider.value} account '{account.name}'",
        changes={"verified": account.verified_at is not None},
        ip_address=client_ip(request),
    )
    await db.commit()
    return _account_out(account, 0)


async def _get_account(db: DbSession, account_id: uuid.UUID) -> ProviderAccount:
    account = await db.get(ProviderAccount, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    return account


@router.patch("/telephony/{account_id}", response_model=TelephonyAccountOut)
async def update_account(
    account_id: uuid.UUID,
    body: TelephonyAccountPatch,
    request: Request,
    admin: AdminUser,
    db: DbSession,
) -> TelephonyAccountOut:
    account = await _get_account(db, account_id)
    changes: dict[str, object] = {}
    if body.name is not None:
        changes["name"] = [account.name, body.name.strip()]
        account.name = body.name.strip()
    if body.max_concurrent_calls is not None:
        changes["max_concurrent_calls"] = [account.max_concurrent_calls, body.max_concurrent_calls]
        account.max_concurrent_calls = body.max_concurrent_calls
    if body.cost_per_minute_inr is not None:
        old = account.settings.get("cost_per_minute_inr")
        changes["cost_per_minute_inr"] = [old, body.cost_per_minute_inr]
        account.settings = {**account.settings, "cost_per_minute_inr": body.cost_per_minute_inr}
    if body.is_active is not None:
        changes["is_active"] = [account.is_active, body.is_active]
        account.is_active = body.is_active
    if body.given():
        merged = decrypt_json(account.encrypted_credentials) | body.given()
        cls = PROVIDERS[account.provider]
        try:
            credentials = cls.validate_credentials(merged)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        account.encrypted_credentials = encrypt_json(credentials)
        account.credential_hint = cls.credential_hint(credentials)
        changes["credentials"] = "updated"
        await verify_account(account)
    if changes:
        await audit.record(
            db,
            actor=admin,
            action="integration.telephony_updated",
            entity_type="provider_account",
            entity_id=account.id,
            summary=f"Updated {account.provider.value} account '{account.name}'",
            changes=changes,
            ip_address=client_ip(request),
        )
    await db.commit()
    numbers = await db.scalar(
        select(func.count())
        .select_from(PhoneNumber)
        .where(PhoneNumber.provider_account_id == account.id)
    )
    return _account_out(account, numbers or 0)


@router.post("/telephony/{account_id}/test", response_model=TelephonyAccountOut)
async def test_account(account_id: uuid.UUID, _: AdminUser, db: DbSession) -> TelephonyAccountOut:
    account = await _get_account(db, account_id)
    await verify_account(account)
    await db.commit()
    numbers = await db.scalar(
        select(func.count())
        .select_from(PhoneNumber)
        .where(PhoneNumber.provider_account_id == account.id)
    )
    return _account_out(account, numbers or 0)


@router.delete("/telephony/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: uuid.UUID, request: Request, admin: AdminUser, db: DbSession
) -> None:
    account = await _get_account(db, account_id)
    numbers = await db.scalar(
        select(func.count())
        .select_from(PhoneNumber)
        .where(PhoneNumber.provider_account_id == account.id)
    )
    if numbers:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Remove this account's phone numbers before disconnecting it"
        )
    await db.delete(account)
    await audit.record(
        db,
        actor=admin,
        action="integration.telephony_removed",
        entity_type="provider_account",
        entity_id=account.id,
        summary=f"Disconnected {account.provider.value} account '{account.name}'",
        ip_address=client_ip(request),
    )
    await db.commit()
