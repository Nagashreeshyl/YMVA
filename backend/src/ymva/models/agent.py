import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ymva.models.base import Base, Timestamps, UUIDPk, str_enum


class AgentStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class Agent(UUIDPk, Timestamps, Base):
    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[AgentStatus] = mapped_column(
        str_enum(AgentStatus, "agent_status"), default=AgentStatus.DRAFT, index=True
    )
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="SET NULL", use_alter=True)
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Work in progress from the editor. Versions are only created when the admin asks.
    draft_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    draft_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentVersion(UUIDPk, Base):
    """Immutable snapshot of an agent's full configuration (prompt, voices, tools, rules)."""

    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int]
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    note: Mapped[str] = mapped_column(String(500), default="")
    source: Mapped[str] = mapped_column(String(32), default="manual")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
