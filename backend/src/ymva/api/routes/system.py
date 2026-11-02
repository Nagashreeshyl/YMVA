"""System health for the dashboard, Prometheus metrics, and where alerts are sent."""

import hmac
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from ymva.api.deps import DbSession, SettingsManager, client_ip
from ymva.config import get_settings
from ymva.models import AppSetting
from ymva.redis import get_redis
from ymva.services import audit, monitoring

router = APIRouter(tags=["system"])


class AlertsIn(BaseModel):
    # Slack, Google Chat or Discord incoming-webhook address; empty turns alerts off.
    webhook_url: str = Field(default="", max_length=500, pattern=r"^(https://.+)?$")


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request, db: DbSession) -> Response:
    """Prometheus scrapes this with METRICS_TOKEN (the public site also refuses the path)."""
    token = get_settings().metrics_token.get_secret_value()
    given = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not token or not hmac.compare_digest(given.encode(), token.encode()):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    text = monitoring.prometheus_text(await monitoring.snapshot(db))
    return Response(text, media_type="text/plain; version=0.0.4")


@router.get("/system/health")
async def system_health(_: SettingsManager, db: DbSession) -> dict[str, Any]:
    snapshot = await monitoring.snapshot(db)
    return {
        "snapshot": monitoring.as_dict(snapshot),
        "problems": monitoring.problems(snapshot),
        "alerting": bool(await monitoring.alert_webhook(db)),
        "alerted": await get_redis().hgetall(monitoring.ALERTS_KEY),
    }


@router.get("/system/alerts")
async def get_alerts(_: SettingsManager, db: DbSession) -> dict[str, str]:
    return {"webhook_url": await monitoring.alert_webhook(db) or ""}


@router.put("/system/alerts")
async def set_alerts(
    body: AlertsIn, request: Request, admin: SettingsManager, db: DbSession
) -> dict[str, str]:
    setting = await db.get(AppSetting, monitoring.ALERT_SETTING)
    if setting is None:
        setting = AppSetting(key=monitoring.ALERT_SETTING, value={})
        db.add(setting)
    setting.value = {"webhook_url": body.webhook_url.strip()}
    await audit.record(
        db,
        actor=admin,
        action="settings.alerts",
        entity_type="setting",
        entity_id=None,
        summary="Turned alerts on" if body.webhook_url else "Turned alerts off",
        ip_address=client_ip(request),
    )
    await db.commit()
    return {"webhook_url": body.webhook_url.strip()}


@router.post("/system/alerts/test")
async def test_alerts(_: SettingsManager, db: DbSession) -> dict[str, bool]:
    url = await monitoring.alert_webhook(db)
    if not url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Save a webhook address first")
    try:
        await monitoring.send_alert(url, "✅ YMVA test alert: alerts will arrive here.")
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"The webhook didn't accept the message: {exc}"
        ) from exc
    return {"ok": True}
