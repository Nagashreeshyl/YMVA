from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from tests.test_calls_api import _no_network, _setup  # noqa: F401  (fixture used below)
from ymva.db import get_sessionmaker
from ymva.models import Call, CallStatus, Campaign, CampaignContact, ContactStatus
from ymva.services import dialer
from ymva.services.analysis import NO_CONVERSATION

# Monday 26 Oct 2026, 11:00 in India.
OPEN = datetime(2026, 10, 26, 5, 30, tzinfo=UTC)
CSV = (
    "Phone,Customer Name,Loan ID\n98450 12345,Ramesh,YM1\n+91 98450 54321,Lakshmi,YM2\n"
    "98450 12345,Dup,YM9\nnot-a-number,X,Y\n"
)


@pytest.fixture
def placed(request: pytest.FixtureRequest) -> list[dict[str, Any]]:
    """Calls the fake provider was asked to place."""
    return request.getfixturevalue("_no_network")  # type: ignore[no-any-return]


async def _campaign(
    client: httpx.AsyncClient, **overrides: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    setup = await _setup(client)
    body = {
        "name": "October interest",
        "agent_id": setup["agent"]["id"],
        "phone_number_ids": [setup["number"]["id"]],
        "max_concurrent": 1,
        "max_attempts": 2,
        "retry_after_minutes": 30,
    } | overrides
    resp = await client.post("/api/campaigns", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json(), setup


async def _contacts() -> list[CampaignContact]:
    async with get_sessionmaker()() as db:
        return list(await db.scalars(select(CampaignContact).order_by(CampaignContact.phone)))


async def _end_call(call_id: Any, status: CallStatus, outcome: str | None = None) -> None:
    async with get_sessionmaker()() as db:
        call = await db.get(Call, call_id)
        assert call is not None
        call.status, call.outcome = status, outcome
        call.ended_at = OPEN
        await db.commit()


async def test_import_maps_columns_and_skips_bad_rows(admin_client: httpx.AsyncClient) -> None:
    campaign, _ = await _campaign(admin_client)
    resp = await admin_client.post(f"/api/campaigns/{campaign['id']}/contacts", json={"csv": CSV})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert (result["added"], result["duplicates"], result["invalid_count"]) == (2, 1, 1)
    assert result["columns"] == ["customer_name", "loan_id"]
    contacts = await _contacts()
    assert contacts[0].phone == "+919845012345"
    assert contacts[0].variables == {"customer_name": "Ramesh", "loan_id": "YM1"}


async def test_dialer_calls_retries_and_finishes(
    admin_client: httpx.AsyncClient, placed: list[dict[str, Any]]
) -> None:
    campaign, _ = await _campaign(admin_client)
    await admin_client.post(f"/api/campaigns/{campaign['id']}/contacts", json={"csv": CSV})
    resp = await admin_client.post(f"/api/campaigns/{campaign['id']}/start")
    assert resp.json()["status"] == "running", resp.text

    await dialer.tick(OPEN)
    assert len(placed) == 1  # max_concurrent = 1
    first = next(c for c in await _contacts() if c.status == ContactStatus.CALLING)
    assert placed[0]["to_number"] == first.phone
    await dialer.tick(OPEN)
    assert len(placed) == 1  # still busy with the first call

    await _end_call(first.last_call_id, CallStatus.NO_ANSWER)
    await dialer.tick(OPEN)  # first -> retry later; second is called
    contacts = {c.phone: c for c in await _contacts()}
    assert contacts[first.phone].status == ContactStatus.RETRY
    assert contacts[first.phone].last_result == "No answer"
    second = next(c for c in contacts.values() if c.status == ContactStatus.CALLING)
    await _end_call(second.last_call_id, CallStatus.COMPLETED, "Promised to pay")

    later = OPEN + timedelta(minutes=31)
    await dialer.tick(later)  # second done; first's retry is due and dialed
    await _end_call(
        (await _contacts_by(first.phone)).last_call_id, CallStatus.COMPLETED, NO_CONVERSATION
    )
    await dialer.tick(later)  # nobody spoke on attempt 2 of 2 -> failed
    await dialer.tick(later)
    contacts = {c.phone: c for c in await _contacts()}
    assert contacts[first.phone].status == ContactStatus.FAILED
    assert contacts[first.phone].attempts == 2
    assert contacts[second.phone].status == ContactStatus.DONE
    out = (await admin_client.get(f"/api/campaigns/{campaign['id']}")).json()
    assert out["status"] == "completed" and out["outcomes"] == {"Promised to pay": 1}

    export = (await admin_client.get(f"/api/campaigns/{campaign['id']}/export.csv")).text
    assert "Promised to pay" in export and "Answered but nobody spoke" in export


async def _contacts_by(phone: str) -> CampaignContact:
    return next(c for c in await _contacts() if c.phone == phone)


async def test_dialer_waits_for_calling_hours(
    admin_client: httpx.AsyncClient, placed: list[dict[str, Any]]
) -> None:
    campaign, _ = await _campaign(admin_client, days=[0, 1, 2, 3, 4], start_time="10:00")
    await admin_client.post(f"/api/campaigns/{campaign['id']}/contacts", json={"csv": CSV})
    await admin_client.post(f"/api/campaigns/{campaign['id']}/start")
    await dialer.tick(datetime(2026, 10, 26, 3, 0, tzinfo=UTC))  # 08:30 IST, too early
    await dialer.tick(datetime(2026, 10, 31, 6, 0, tzinfo=UTC))  # Saturday
    assert placed == []
    await dialer.tick(OPEN)
    assert len(placed) == 1


async def test_start_needs_published_agent_and_contacts(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign, _ = await _campaign(admin_client)
    resp = await admin_client.post(f"/api/campaigns/{campaign['id']}/start")
    assert resp.status_code == 400 and "contacts" in resp.json()["detail"]
    async with get_sessionmaker()() as db:
        assert (await db.get(Campaign, campaign["id"])) is not None
