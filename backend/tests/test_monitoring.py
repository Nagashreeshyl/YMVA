import json
from typing import Any

import httpx
import pytest

from ymva.db import get_sessionmaker
from ymva.models import Call, CallDirection, CallStatus
from ymva.services import monitoring


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Messages sent to the alert webhook."""
    sent: list[dict[str, Any]] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200)

    monkeypatch.setattr(
        monitoring.httpx,
        "AsyncClient",
        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k),
    )
    return sent


async def test_metrics_only_inside_the_server(client: httpx.AsyncClient) -> None:
    inside = await client.get("/api/metrics")
    assert inside.status_code == 200 and "ymva_calls_active 0" in inside.text
    via_proxy = await client.get("/api/metrics", headers={"X-Forwarded-For": "203.0.113.9"})
    assert via_proxy.status_code == 404


def test_problems_cover_each_check() -> None:
    healthy = monitoring.Snapshot(
        calls_active=1,
        calls_last_hour={"completed": 9, "failed": 1},
        calls_today=10,
        errors_last_15m=0,
        reply_p50_seconds=1.1,
        cost_today_usd=0.2,
        campaigns_running=1,
        contacts_waiting=5,
        dialer_idle_seconds=4,
        draining=False,
        backup_age_hours=3,
    )
    assert monitoring.problems(healthy) == {}
    broken = monitoring.Snapshot(
        **{
            **vars(healthy),
            "calls_last_hour": {"completed": 3, "failed": 3},
            "errors_last_15m": 12,
            "dialer_idle_seconds": 400,
            "backup_age_hours": None,
            "failing_accounts": ["Plivo main"],
        }
    )
    assert set(monitoring.problems(broken)) == {
        "failed_calls",
        "service_errors",
        "dialer_stalled",
        "backup_stale",
        "account:Plivo main",
    }


async def test_alerts_fire_once_and_resolve(
    admin_client: httpx.AsyncClient, posted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = await admin_client.put(
        "/api/system/alerts", json={"webhook_url": "https://hooks.slack.com/services/T/B/x"}
    )
    assert resp.status_code == 200
    assert (await admin_client.post("/api/system/alerts/test")).json() == {"ok": True}
    assert "test alert" in posted[-1]["text"]

    monkeypatch.setattr(monitoring, "_newest_backup_age_hours", lambda: 2.0)
    async with get_sessionmaker()() as db:
        for status in [CallStatus.FAILED] * 3 + [CallStatus.COMPLETED] * 2:
            db.add(Call(direction=CallDirection.OUTBOUND, status=status))
        await db.commit()
    await monitoring.check_and_alert()
    await monitoring.check_and_alert()  # still failing: no repeat
    alerts = [p["text"] for p in posted[1:]]
    assert len(alerts) == 1 and "3 of 5 calls in the last hour failed" in alerts[0]

    async with get_sessionmaker()() as db:
        for _ in range(10):
            db.add(Call(direction=CallDirection.OUTBOUND, status=CallStatus.COMPLETED))
        await db.commit()
    await monitoring.check_and_alert()
    assert "resolved" in posted[-1]["text"]

    health = (await admin_client.get("/api/system/health")).json()
    assert health["problems"] == {} and health["alerting"] is True
