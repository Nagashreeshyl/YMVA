from typing import Any

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ymva.models.base import Base, Timestamps


class AppSetting(Timestamps, Base):
    """Small app-wide settings by name, e.g. where alerts are sent."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
