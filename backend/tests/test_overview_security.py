from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from ymva.db import get_sessionmaker
from ymva.logging import _redact
from ymva.models import Call, CallDirection, CallStatus
from ymva.security import decrypt_json, encrypt_json


async def test_overview_empty(admin_client: httpx.AsyncClient) -> None:
    data = (await admin_client.get("/api/overview")).json()
    assert data["calls_today"] == 0
    assert data["connect_rate_7d"] is None
    assert len(data["daily"]) == 7
    assert all(point["calls"] == 0 for point in data["daily"])


async def test_overview_counts_calls(admin_client: httpx.AsyncClient) -> None:
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        session.add_all(
            [
                Call(
                    direction=CallDirection.OUTBOUND,
                    status=CallStatus.COMPLETED,
                    duration_seconds=120,
                    outcome="promise_to_pay",
                    languages=["kn", "en"],
                    cost_total=Decimal("0.08"),
                ),
                Call(
                    direction=CallDirection.OUTBOUND,
                    status=CallStatus.NO_ANSWER,
                    languages=[],
                ),
                Call(
                    direction=CallDirection.INBOUND, status=CallStatus.IN_PROGRESS, languages=["te"]
                ),
                Call(
                    direction=CallDirection.OUTBOUND,
                    status=CallStatus.COMPLETED,
                    created_at=now - timedelta(days=30),
                ),
            ]
        )
        await session.commit()

    data = (await admin_client.get("/api/overview")).json()
    assert data["calls_7d"] == 3
    assert data["completed_7d"] == 1
    assert data["active_calls"] == 1
    assert abs(data["connect_rate_7d"] - 1 / 3) < 1e-9
    assert data["avg_duration_seconds_7d"] == 120
    assert abs(data["cost_7d"] - 0.08) < 1e-9
    assert abs(data["cost_per_minute_7d"] - 0.04) < 1e-9  # $0.08 over 2 minutes
    assert data["outcomes_7d"] == {"promise_to_pay": 1}
    assert data["languages_7d"] == {"kn": 1, "en": 1, "te": 1}
    assert sum(point["calls"] for point in data["daily"]) == 3


def test_credentials_encryption_roundtrip() -> None:
    secret = {"auth_id": "MA123", "auth_token": "s3cret"}
    token = encrypt_json(secret)
    assert "s3cret" not in token
    assert decrypt_json(token) == secret


def test_logs_redact_secrets_and_mask_phone_numbers() -> None:
    event = _redact(None, "info", {"event": "dialing +919876543210", "auth_token": "abc"})
    assert event["auth_token"] == "[redacted]"
    assert "9876543210" not in event["event"]
    assert event["event"] == "dialing ***3210"
    dated = _redact(None, "info", {"event": "on 2026-10-25 call 3f2a1b9c-0000"})
    assert dated["event"] == "on 2026-10-25 call 3f2a1b9c-0000"


def test_voice_library_logs_keep_customer_details_out(capfd: pytest.CaptureFixture[str]) -> None:
    from loguru import logger

    from ymva.logging import configure_logging

    configure_logging()  # LOG_LEVEL is INFO in tests, as in production
    logger.debug("Setting system instruction: customer Ramesh owes 50000")
    logger.info("Calling +919845012345")
    err = capfd.readouterr().err
    assert "Ramesh" not in err and "+919845012345" not in err and "***2345" in err
