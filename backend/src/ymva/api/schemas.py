import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, field_validator

from ymva.models import Role
from ymva.permissions import ROLE_PERMS
from ymva.security import MIN_PASSWORD_LENGTH


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    name: str
    role: Role
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def permissions(self) -> list[str]:
        """What this user may do; the dashboard hides everything else."""
        return sorted(ROLE_PERMS[self.role])


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


def _check_password(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    return value


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)

    _validate = field_validator("new_password")(_check_password)


class UserCreateIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    role: Role = Role.VIEWER
    password: str = Field(max_length=256)

    _validate = field_validator("password")(_check_password)


class UserUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, max_length=256)

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str | None) -> str | None:
        return None if value is None else _check_password(value)


class AuditLogOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    actor_label: str
    action: str
    entity_type: str
    entity_id: str | None
    summary: str
    changes: dict[str, Any]


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class DailyPoint(BaseModel):
    date: str
    calls: int
    completed: int


class OverviewOut(BaseModel):
    calls_today: int
    calls_7d: int
    completed_7d: int
    connect_rate_7d: float | None
    avg_duration_seconds_7d: float | None
    cost_7d: float | None  # USD, AI plus phone line; None when the role hides costs
    cost_per_minute_7d: float | None  # USD per minute of talk time
    active_calls: int
    agents_active: int
    agents_total: int
    providers_connected: int
    phone_numbers: int
    daily: list[DailyPoint]
    outcomes_7d: dict[str, int]
    languages_7d: dict[str, int]
