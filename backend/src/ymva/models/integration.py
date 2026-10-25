import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ymva.models.base import Base, Timestamps, UUIDPk, str_enum


class AIProvider(StrEnum):
    SARVAM = "sarvam"
    GOOGLE = "google"


class AICredential(UUIDPk, Timestamps, Base):
    """API key for a speech or language-model provider. One per provider, encrypted at rest."""

    __tablename__ = "ai_credentials"

    provider: Mapped[AIProvider] = mapped_column(str_enum(AIProvider, "ai_provider"), unique=True)
    encrypted_key: Mapped[str] = mapped_column(Text)
    key_hint: Mapped[str] = mapped_column(String(16))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
