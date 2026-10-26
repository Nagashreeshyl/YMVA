"""System health: one snapshot used by the metrics endpoint, the Settings page and alerts.

Alerts go to a webhook (Slack, Google Chat, Discord or anything that accepts JSON) when a
check starts failing, and again when it recovers. A Redis lock makes one process send them.
"""

import asyncio
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.db import get_sessionmaker
from ymva.logging import get_logger
from ymva.models import (
    AICredential,
    AppSetting,
    Call,
    CallEvent,
    CallEventType,
    CallStatus,
    Campaign,
    CampaignContact,
    CampaignStatus,
    ContactStatus,
    ProviderAccount,
)
from ymva.redis import get_redis
from ymva.services.calls import ACTIVE_STATUSES
from ymva.services.dialer import DIALER_TICK_KEY
from ymva.services.drain import is_draining

log = get_logger(__name__)

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/backups"))
ALERTS_KEY = "alerts:firing"
ALERT_SETTING = "alerts"
CHECK_EVERY_SECONDS = 60
LOCK_KEY = "monitor:lock"


@dataclass
class Snapshot:
    calls_active: int
    calls_last_hour: dict[str, int]  # by status
    calls_today: int
    errors_last_15m: int
    reply_p50_seconds: float | None  # typical response time of calls ended in the last hour
    cost_today_usd: float
    campaigns_running: int
    contacts_waiting: int
    dialer_idle_seconds: float | None  # since the dialer last ran; None if it never ran
    draining: bool
    backup_age_hours: float | None
    failing_accounts: list[str] = field(default_factory=list)
    failing_ai_keys: list[str] = field(default_factory=list)


def _newest_backup_age_hours() -> float | None:
    try:
        newest = max((p.stat().st_mtime for p in BACKUP_DIR.glob("*.sql.gz")), default=None)
    except OSError:
        return None
    return None if newest is None else round((time.time() - newest) / 3600, 1)


async def snapshot(db: AsyncSession) -> Snapshot:
    now = datetime.now(UTC)
    hour_ago, quarter_ago = now - timedelta(hours=1), now - timedelta(minutes=15)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def count(*where: Any) -> Any:
        return select(func.count()).select_from(Call).where(*where)

    last_hour = dict(
        (
            await db.execute(
                select(Call.status, func.count())
                .where(Call.created_at >= hour_ago)
                .group_by(Call.status)
            )
        ).all()
    )
    recent = await db.scalars(
        select(Call.latency).where(Call.ended_at >= hour_ago, Call.latency.is_not(None))
    )
    p50s = [lat["response"]["p50_ms"] for lat in recent if lat and lat.get("response")]
    tick = await get_redis().get(DIALER_TICK_KEY)
    return Snapshot(
        calls_active=await db.scalar(count(Call.status.in_(ACTIVE_STATUSES))) or 0,
        calls_last_hour={
            str(k.value if hasattr(k, "value") else k): v for k, v in last_hour.items()
        },
        calls_today=await db.scalar(count(Call.created_at >= day_start)) or 0,
        errors_last_15m=await db.scalar(
            select(func.count())
            .select_from(CallEvent)
            .where(CallEvent.type == CallEventType.ERROR, CallEvent.at >= quarter_ago)
        )
        or 0,
        reply_p50_seconds=round(sorted(p50s)[len(p50s) // 2] / 1000, 2) if p50s else None,
        cost_today_usd=float(
            await db.scalar(
                select(func.coalesce(func.sum(Call.cost_total), 0)).where(
                    Call.created_at >= day_start
                )
            )
            or 0
        ),
        campaigns_running=await db.scalar(
            select(func.count())
            .select_from(Campaign)
            .where(Campaign.status == CampaignStatus.RUNNING)
        )
        or 0,
        contacts_waiting=await db.scalar(
            select(func.count())
            .select_from(CampaignContact)
            .join(Campaign, Campaign.id == CampaignContact.campaign_id)
            .where(
                Campaign.status == CampaignStatus.RUNNING,
                CampaignContact.status.in_([ContactStatus.PENDING, ContactStatus.RETRY]),
            )
        )
        or 0,
        dialer_idle_seconds=round(time.time() - float(tick), 1) if tick else None,
        draining=await is_draining(),
        backup_age_hours=_newest_backup_age_hours(),
        failing_accounts=[
            a.name
            for a in await db.scalars(
                select(ProviderAccount).where(
                    ProviderAccount.is_active, ProviderAccount.last_error.is_not(None)
                )
            )
        ],
        failing_ai_keys=[
            c.provider.value
            for c in await db.scalars(
                select(AICredential).where(AICredential.last_error.is_not(None))
            )
        ],
    )


def _num(value: float | None) -> str:
    return "NaN" if value is None else str(value)


def prometheus_text(s: Snapshot) -> str:
    """The snapshot in Prometheus' text format."""
    lines = [
        "# HELP ymva_calls_active Calls ringing or in progress.",
        "# TYPE ymva_calls_active gauge",
        f"ymva_calls_active {s.calls_active}",
        "# HELP ymva_calls_last_hour Calls started in the last hour, by status.",
        "# TYPE ymva_calls_last_hour gauge",
        *(
            f'ymva_calls_last_hour{{status="{k}"}} {v}'
            for k, v in sorted(s.calls_last_hour.items())
        ),
        "# TYPE ymva_calls_today gauge",
        f"ymva_calls_today {s.calls_today}",
        "# HELP ymva_call_errors_15m Service errors logged on calls in the last 15 minutes.",
        "# TYPE ymva_call_errors_15m gauge",
        f"ymva_call_errors_15m {s.errors_last_15m}",
        "# HELP ymva_reply_seconds_p50 Typical time to reply, calls ended in the last hour.",
        "# TYPE ymva_reply_seconds_p50 gauge",
        f"ymva_reply_seconds_p50 {_num(s.reply_p50_seconds)}",
        "# TYPE ymva_cost_today_usd gauge",
        f"ymva_cost_today_usd {s.cost_today_usd}",
        "# TYPE ymva_campaigns_running gauge",
        f"ymva_campaigns_running {s.campaigns_running}",
        "# TYPE ymva_campaign_contacts_waiting gauge",
        f"ymva_campaign_contacts_waiting {s.contacts_waiting}",
        "# HELP ymva_dialer_idle_seconds Seconds since the campaign dialer last ran.",
        "# TYPE ymva_dialer_idle_seconds gauge",
        f"ymva_dialer_idle_seconds {_num(s.dialer_idle_seconds)}",
        "# TYPE ymva_draining gauge",
        f"ymva_draining {int(s.draining)}",
        "# HELP ymva_backup_age_hours Age of the newest database backup.",
        "# TYPE ymva_backup_age_hours gauge",
        f"ymva_backup_age_hours {_num(s.backup_age_hours)}",
        "# TYPE ymva_failing_integrations gauge",
        f"ymva_failing_integrations {len(s.failing_accounts) + len(s.failing_ai_keys)}",
    ]
    return "\n".join(lines) + "\n"


def problems(s: Snapshot) -> dict[str, str]:
    """Checks that are failing now, by name, with a plain-words message."""
    found: dict[str, str] = {}
    total = sum(s.calls_last_hour.values())
    failed = s.calls_last_hour.get(CallStatus.FAILED.value, 0)
    if total >= 5 and failed / total >= 0.3:
        found["failed_calls"] = f"{failed} of {total} calls in the last hour failed to connect"
    if s.errors_last_15m >= 10:
        found["service_errors"] = (
            f"{s.errors_last_15m} AI/provider errors on calls in the last 15 minutes "
            "(often a quota or rate limit; see the calls' transcripts)"
        )
    if (
        s.campaigns_running
        and not s.draining
        and (s.dialer_idle_seconds is None or s.dialer_idle_seconds > 120)
    ):
        found["dialer_stalled"] = "Campaigns are running but the dialer hasn't run for 2+ minutes"
    if s.backup_age_hours is None or s.backup_age_hours > 30:
        found["backup_stale"] = (
            "No database backup found"
            if s.backup_age_hours is None
            else f"The newest database backup is {s.backup_age_hours:.0f} hours old"
        )
    for name in s.failing_accounts:
        found[f"account:{name}"] = f"Telephony account '{name}' fails its connection check"
    for name in s.failing_ai_keys:
        found[f"ai:{name}"] = f"The {name} API key fails its check"
    return found


async def alert_webhook(db: AsyncSession) -> str | None:
    setting = await db.get(AppSetting, ALERT_SETTING)
    url = (setting.value if setting else {}).get("webhook_url")
    return str(url) if url else None


async def send_alert(url: str, text: str) -> None:
    # "text" for Slack and Google Chat, "content" for Discord.
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json={"text": text, "content": text})
    response.raise_for_status()


async def check_and_alert() -> None:
    async with get_sessionmaker()() as db:
        current = problems(await snapshot(db))
        url = await alert_webhook(db)
    redis = get_redis()
    stored = await redis.hgetall(ALERTS_KEY)
    firing: dict[str, str] = {str(k): str(v) for k, v in stored.items()}
    started = {k: v for k, v in current.items() if k not in firing}
    resolved = [k for k in firing if k not in current]
    if url and (started or resolved):
        lines = [f"⚠️ YMVA: {message}" for message in started.values()]
        lines += [f"✅ YMVA: resolved — {firing[k]}" for k in resolved]
        try:
            await send_alert(url, "\n".join(lines))
        except httpx.HTTPError as exc:
            log.warning("alert webhook failed", error=str(exc))
            return  # try again next round
    for k in resolved:
        await redis.hdel(ALERTS_KEY, k)
    if started:
        await redis.hset(ALERTS_KEY, mapping=dict(started))  # type: ignore[arg-type]


async def run_forever() -> None:
    """Started with the API; checks once a minute, one process at a time."""
    redis = get_redis()
    await asyncio.sleep(30)  # let the dialer run once after a restart
    while True:
        try:
            if await redis.set(LOCK_KEY, "1", nx=True, ex=CHECK_EVERY_SECONDS - 5):
                await check_and_alert()
        except Exception:
            log.exception("health check failed")
        await asyncio.sleep(CHECK_EVERY_SECONDS)


def as_dict(s: Snapshot) -> dict[str, Any]:
    return asdict(s)
