import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ymva.models.base import Base, Timestamps, UUIDPk, str_enum


class ToolType(StrEnum):
    HTTP = "http"
    GOOGLE_SHEETS = "google_sheets"
    DATABASE = "database"
    MCP = "mcp"
    BUILTIN = "builtin"


class Tool(UUIDPk, Timestamps, Base):
    """A callable tool agents can use mid-call. Secrets live in `encrypted_secrets`."""

    __tablename__ = "tools"

    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[ToolType] = mapped_column(str_enum(ToolType, "tool_type"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    parameters_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    encrypted_secrets: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
