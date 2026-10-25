from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from sqlalchemy import ColumnElement, Date, Select, cast, func, select, text

from ymva.api.deps import CurrentUser, DbSession
from ymva.api.schemas import DailyPoint, OverviewOut
from ymva.config import get_settings
from ymva.models import Agent, AgentStatus, Call, CallStatus, PhoneNumber, ProviderAccount

router = APIRouter(prefix="/overview", tags=["overview"])

ACTIVE_STATUSES = (CallStatus.RINGING, CallStatus.IN_PROGRESS)


@router.get("", response_model=OverviewOut)
async def overview(_: CurrentUser, db: DbSession) -> OverviewOut:
    tz_name = get_settings().timezone
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today - timedelta(days=6)
    in_week = Call.created_at >= week_start

    def count(*where: ColumnElement[bool]) -> Select[int]:
        return select(func.count()).select_from(Call).where(*where)

    calls_today = await db.scalar(count(Call.created_at >= today)) or 0
    calls_7d = await db.scalar(count(in_week)) or 0
    completed_7d = await db.scalar(count(in_week, Call.status == CallStatus.COMPLETED)) or 0
    active_calls = await db.scalar(count(Call.status.in_(ACTIVE_STATUSES))) or 0
    avg_duration = await db.scalar(
        select(func.avg(Call.duration_seconds)).where(in_week, Call.duration_seconds.is_not(None))
    )
    cost_7d = await db.scalar(select(func.coalesce(func.sum(Call.cost_total), 0)).where(in_week))

    local_day = cast(func.timezone(tz_name, Call.created_at), Date)
    daily_rows = (
        await db.execute(
            select(
                local_day.label("day"),
                func.count().label("calls"),
                func.count().filter(Call.status == CallStatus.COMPLETED).label("completed"),
            )
            .where(in_week)
            .group_by("day")
        )
    ).all()
    by_day = {row.day.isoformat(): row for row in daily_rows}
    daily = []
    for i in range(7):
        day = (week_start + timedelta(days=i)).date().isoformat()
        row = by_day.get(day)
        daily.append(
            DailyPoint(
                date=day, calls=row.calls if row else 0, completed=row.completed if row else 0
            )
        )

    outcomes = dict(
        (
            await db.execute(
                select(Call.outcome, func.count())
                .where(in_week, Call.outcome.is_not(None))
                .group_by(Call.outcome)
            )
        ).all()
    )
    languages: dict[str, int] = dict(
        (
            await db.execute(
                text(
                    "select lang, count(*) from calls, jsonb_array_elements_text(languages) lang"
                    " where created_at >= :start group by lang"
                ),
                {"start": week_start},
            )
        ).all()
    )

    return OverviewOut(
        calls_today=calls_today,
        calls_7d=calls_7d,
        completed_7d=completed_7d,
        connect_rate_7d=(completed_7d / calls_7d) if calls_7d else None,
        avg_duration_seconds_7d=float(avg_duration) if avg_duration is not None else None,
        cost_7d=float(cost_7d or 0),
        active_calls=active_calls,
        agents_active=await db.scalar(
            select(func.count()).select_from(Agent).where(Agent.status == AgentStatus.ACTIVE)
        )
        or 0,
        agents_total=await db.scalar(
            select(func.count()).select_from(Agent).where(Agent.status != AgentStatus.ARCHIVED)
        )
        or 0,
        providers_connected=await db.scalar(
            select(func.count()).select_from(ProviderAccount).where(ProviderAccount.is_active)
        )
        or 0,
        phone_numbers=await db.scalar(
            select(func.count()).select_from(PhoneNumber).where(PhoneNumber.is_active)
        )
        or 0,
        daily=daily,
        outcomes_7d=outcomes,
        languages_7d=languages,
    )
