import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ymva.models.base import Base, Timestamps, UUIDPk, str_enum


class TelephonyProvider(StrEnum):
    PLIVO = "plivo"
    VOBIZ = "vobiz"
    EXOTEL = "exotel"
    TWILIO = "twilio"
    TELNYX = "telnyx"
    VONAGE = "vonage"
    SIP = "sip"


class ProviderAccount(UUIDPk, Timestamps, Base):
    """A connected telephony account. Credentials are encrypted at rest."""

    __tablename__ = "provider_accounts"

    provider: Mapped[TelephonyProvider] = mapped_column(
        str_enum(TelephonyProvider, "telephony_provider"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    encrypted_credentials: Mapped[str] = mapped_column(Text)
    credential_hint: Mapped[str] = mapped_column(String(32), default="")
    # Secret path segment in webhook URLs, so only this provider can reach them.
    webhook_secret: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    max_concurrent_calls: Mapped[int] = mapped_column(default=10)
    is_active: Mapped[bool] = mapped_column(default=True)


class PhoneNumber(UUIDPk, Timestamps, Base):
    __tablename__ = "phone_numbers"

    provider_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_accounts.id", ondelete="CASCADE"), index=True
    )
    e164: Mapped[str] = mapped_column(String(20), unique=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    inbound_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    max_concurrent_calls: Mapped[int] = mapped_column(default=5)
    is_active: Mapped[bool] = mapped_column(default=True)
