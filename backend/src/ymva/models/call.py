import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ymva.models.base import Base, Timestamps, UUIDPk, str_enum


class CallDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    WEB = "web"


class CallStatus(StrEnum):
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    FAILED = "failed"
    CANCELED = "canceled"


class Call(UUIDPk, Timestamps, Base):
    __tablename__ = "calls"
    __table_args__ = (
        UniqueConstraint("provider_account_id", "provider_call_id"),
        Index("ix_calls_agent_created", "agent_id", "created_at"),
        Index("ix_calls_created_at", "created_at"),
    )

    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    # Kept so call history still names the agent after it is deleted.
    agent_name: Mapped[str | None] = mapped_column(String(120))
    # Who placed it from the dashboard (Callers see only their own calls); None for campaigns
    # and inbound calls.
    placed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # Where to POST the result when the call ends (set by apps that start calls by API).
    callback_url: Mapped[str | None] = mapped_column(String(2048))
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="SET NULL")
    )
    direction: Mapped[CallDirection] = mapped_column(str_enum(CallDirection, "call_direction"))
    status: Mapped[CallStatus] = mapped_column(
        str_enum(CallStatus, "call_status"), default=CallStatus.QUEUED, index=True
    )
    provider_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("provider_accounts.id", ondelete="SET NULL")
    )
    phone_number_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("phone_numbers.id", ondelete="SET NULL")
    )
    provider_call_id: Mapped[str | None] = mapped_column(String(128))
    from_number: Mapped[str | None] = mapped_column(String(32))
    to_number: Mapped[str | None] = mapped_column(String(32), index=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None]
    end_reason: Mapped[str | None] = mapped_column(String(64))

    languages: Mapped[list[str]] = mapped_column(JSONB, default=list)
    variables: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Exact agent configuration used, when it wasn't a saved version (browser tests of a draft).
    agent_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    outcome: Mapped[str | None] = mapped_column(String(64), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    extracted: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    recording_key: Mapped[str | None] = mapped_column(String(500))

    cost_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cost_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    latency: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class CallEventType(StrEnum):
    USER_TURN = "user_turn"
    AGENT_TURN = "agent_turn"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    LANGUAGE_SWITCH = "language_switch"
    STATUS = "status"
    ERROR = "error"


class CallEvent(UUIDPk, Base):
    """Ordered transcript + timeline of a call (turns, tool calls, language switches)."""

    __tablename__ = "call_events"
    __table_args__ = (UniqueConstraint("call_id", "seq"),)

    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    seq: Mapped[int]
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    type: Mapped[CallEventType] = mapped_column(str_enum(CallEventType, "call_event_type"))
    language: Mapped[str | None] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
