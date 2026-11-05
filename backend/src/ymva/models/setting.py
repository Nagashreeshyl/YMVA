import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ymva.models.base import Base, Timestamps, UUIDPk


class AppSetting(Timestamps, Base):
    """Small app-wide settings by name, e.g. where alerts are sent."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class DoNotCall(Timestamps, Base):
    """A phone number that must never be called by YMVA (the customer asked not to be)."""

    __tablename__ = "do_not_call"

    phone: Mapped[str] = mapped_column(String(20), primary_key=True)  # E.164
    reason: Mapped[str] = mapped_column(String(200), default="")
    # Asked for on a call and waiting for an admin to approve; not blocked until approved.
    pending: Mapped[bool] = mapped_column(default=False, server_default=false())
    call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"))


class Branch(UUIDPk, Timestamps, Base):
    """A Yellow Metal branch and its manager: calls are transferred to the manager of the
    branch where the customer took their loan (the call's "branch" detail)."""

    __tablename__ = "branches"

    name: Mapped[str] = mapped_column(String(80), unique=True)
    # Other ways apps may write it, e.g. "Bengaluru" for Bangalore.
    other_names: Mapped[list[str]] = mapped_column(JSONB, default=list)
    manager_name: Mapped[str] = mapped_column(String(120), default="")
    manager_phone: Mapped[str] = mapped_column(String(20))  # E.164
    # Its state picks the opening language of calls for this branch (Karnataka -> Kannada).
    state: Mapped[str] = mapped_column(String(60), default="", server_default="")
