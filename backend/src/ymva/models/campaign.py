import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ymva.models.base import Base, Timestamps, UUIDPk, str_enum


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


class Campaign(UUIDPk, Timestamps, Base):
    """An outbound calling list: one agent calls every contact, within calling hours."""

    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(String(120))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    # Caller IDs to dial from, used in turn.
    phone_number_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[CampaignStatus] = mapped_column(
        str_enum(CampaignStatus, "campaign_status"), default=CampaignStatus.DRAFT, index=True
    )
    # Calling hours in the company's time zone: weekdays (0 = Monday) and "HH:MM" times.
    days: Mapped[list[int]] = mapped_column(JSONB, default=lambda: [0, 1, 2, 3, 4, 5])
    start_time: Mapped[str] = mapped_column(String(5), default="09:30")
    end_time: Mapped[str] = mapped_column(String(5), default="19:00")
    max_concurrent: Mapped[int] = mapped_column(default=3)
    max_attempts: Mapped[int] = mapped_column(default=3)
    retry_after_minutes: Mapped[int] = mapped_column(default=60)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why dialing is held up right now (e.g. the agent has no published version).
    last_error: Mapped[str | None] = mapped_column(String(300))


class ContactStatus(StrEnum):
    PENDING = "pending"  # not called yet
    CALLING = "calling"  # a call is ringing or in progress
    RETRY = "retry"  # will be called again at next_attempt_at
    DONE = "done"  # reached: the call connected
    FAILED = "failed"  # not reached after every attempt, or the number is invalid


class CampaignContact(UUIDPk, Base):
    __tablename__ = "campaign_contacts"
    __table_args__ = (
        UniqueConstraint("campaign_id", "phone"),
        Index("ix_campaign_contacts_due", "campaign_id", "status", "next_attempt_at"),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    phone: Mapped[str] = mapped_column(String(20))
    # Customer details for the agent's {{placeholders}}, from the imported columns.
    variables: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[ContactStatus] = mapped_column(
        str_enum(ContactStatus, "contact_status"), default=ContactStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calls.id", ondelete="SET NULL")
    )
    # Why the last attempt didn't connect (no answer, busy, provider error...).
    last_result: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
