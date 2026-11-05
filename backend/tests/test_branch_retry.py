"""Branch required, the branch's state picking the language, single-call retries, voicemail,
and the audit log export."""

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tests.test_calls_api import _no_network, _setup  # noqa: F401  (fixture used by every test)
from ymva.db import get_sessionmaker
from ymva.models import Call, CallStatus
from ymva.services import callback, retries
from ymva.voice.config import AgentConfig
from ymva.voice.prompts import opening_language
from ymva.voice.voicemail import VOICEMAIL, is_voicemail


async def _configure(client: httpx.AsyncClient, agent: dict[str, Any], **changes: Any) -> None:
    config = agent["config"] | changes
    assert (
        await client.put(f"/api/agents/{agent['id']}/draft", json={"config": config})
    ).is_success
    assert (await client.post(f"/api/agents/{agent['id']}/publish", json={})).is_success


async def _branch(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/branches",
        json={
            "name": "Bangalore",
            "other_names": ["Bengaluru"],
            "manager_phone": "+919845000001",
            "state": "Karnataka",
        },
    )
    assert resp.status_code == 201, resp.text


def _call(setup: dict[str, Any], **variables: str) -> dict[str, Any]:
    return {
        "agent_id": setup["agent"]["id"],
        "from_number_id": setup["number"]["id"],
        "to": "+919845011111",
        "variables": {"customer_name": "Ravi", "loan_id": "YM1", "due_amount": "500",
                      "due_date": "2026-11-20"} | variables,
    }  # fmt: skip


async def test_branch_required_blocks_calls_without_a_listed_branch(
    admin_client: httpx.AsyncClient,
) -> None:
    setup = await _setup(admin_client)
    await _branch(admin_client)
    await _configure(admin_client, setup["agent"], branch_required=True)

    missing = await admin_client.post("/api/calls/outbound", json=_call(setup))
    assert missing.status_code == 400 and "branch" in missing.text
    unknown = await admin_client.post("/api/calls/outbound", json=_call(setup, branch="Mysore"))
    assert unknown.status_code == 400 and "Mysore" in unknown.text

    # Any listed spelling works; the call is filed under the listed name, with its state.
    ok = await admin_client.post("/api/calls/outbound", json=_call(setup, branch="bengaluru"))
    assert ok.status_code == 201, ok.text
    assert ok.json()["branch"] == "Bangalore"
    async with get_sessionmaker()() as db:
        call = await db.get(Call, uuid.UUID(ok.json()["id"]))
        assert call is not None
        assert call.variables["branch"] == "Bangalore" and call.variables["state"] == "Karnataka"

    # The manager app lists one branch's calls, by any of its names.
    listed = (await admin_client.get("/api/calls", params={"branch": "Bengaluru"})).json()
    assert [c["id"] for c in listed["items"]] == [ok.json()["id"]]
    assert (await admin_client.get("/api/calls", params={"branch": "Chennai"})).json()["total"] == 0


def test_branch_state_picks_the_opening_language() -> None:
    config = AgentConfig(system_prompt="x", languages=["en", "kn", "te", "ta"])
    assert opening_language(config, {"state": "Karnataka"}) == "kn"
    assert opening_language(config, {"state": "Andhra Pradesh"}) == "te"
    assert opening_language(config, {"state": "Tamil Nadu"}) == "ta"
    # The customer's own preferred language still comes first.
    assert opening_language(config, {"state": "Karnataka", "preferred_language": "ta"}) == "ta"


async def test_unknown_branch_state_is_refused(admin_client: httpx.AsyncClient) -> None:
    resp = await admin_client.post(
        "/api/branches",
        json={"name": "X", "manager_phone": "+919845000002", "state": "Atlantis"},
    )
    assert resp.status_code == 422


async def _end(call_id: str, status: CallStatus, **fields: Any) -> None:
    async with get_sessionmaker()() as db:
        call = await db.get(Call, uuid.UUID(call_id))
        assert call is not None
        call.status, call.ended_at = status, datetime.now(UTC)
        for k, v in fields.items():
            setattr(call, k, v)
        await db.commit()


async def _tick(now: datetime) -> None:
    async with get_sessionmaker()() as db:
        await retries.tick(db, now)


async def _attempts(first_id: str) -> list[Call]:
    async with get_sessionmaker()() as db:
        from sqlalchemy import or_, select

        rows = await db.scalars(
            select(Call)
            .where(or_(Call.id == uuid.UUID(first_id), Call.first_call_id == uuid.UUID(first_id)))
            .order_by(Call.attempt)
        )
        return list(rows)


async def test_unreached_calls_are_retried_up_to_the_limit(
    admin_client: httpx.AsyncClient,
    _no_network: list[dict[str, Any]],  # noqa: F811 (the imported fixture)
) -> None:
    setup = await _setup(admin_client)
    await _configure(
        admin_client,
        setup["agent"],
        retry={"enabled": True, "max_retries": 2, "wait_minutes": 30},
    )
    first = (await admin_client.post("/api/calls/outbound", json=_call(setup))).json()
    now = datetime.now(UTC)

    await _end(first["id"], CallStatus.NO_ANSWER)
    async with get_sessionmaker()() as db:
        call = await db.get(Call, uuid.UUID(first["id"]))
        assert call is not None
        body = callback.payload(call)
        assert body["will_retry"] and body["attempt"] == 1 and body["retry_in_minutes"] == 30
    await _tick(now)
    assert len(_no_network) == 1  # scheduled, not yet due
    await _tick(now + timedelta(minutes=31))
    calls = await _attempts(first["id"])
    assert [c.attempt for c in calls] == [1, 2] and len(_no_network) == 2
    assert calls[1].variables == calls[0].variables

    # Attempt 2 reaches voicemail: retried again. Attempt 3 is the last allowed.
    await _end(str(calls[1].id), CallStatus.COMPLETED, end_reason=VOICEMAIL, outcome="Voicemail")
    await _tick(now + timedelta(minutes=32))
    await _tick(now + timedelta(minutes=63))
    calls = await _attempts(first["id"])
    assert [c.attempt for c in calls] == [1, 2, 3]
    await _end(str(calls[2].id), CallStatus.BUSY)
    await _tick(now + timedelta(minutes=64))
    await _tick(now + timedelta(minutes=200))
    calls = await _attempts(first["id"])
    assert len(calls) == 3 and calls[2].retry_status == "none"

    listed = (await admin_client.get("/api/calls", params={"first_call_id": first["id"]})).json()
    assert sorted(c["attempt"] for c in listed["items"]) == [1, 2, 3]


async def test_reached_calls_and_agents_without_retry_are_not_retried(
    admin_client: httpx.AsyncClient,
    _no_network: list[dict[str, Any]],  # noqa: F811 (the imported fixture)
) -> None:
    setup = await _setup(admin_client)
    plain = (await admin_client.post("/api/calls/outbound", json=_call(setup))).json()
    await _end(plain["id"], CallStatus.NO_ANSWER)
    await _configure(admin_client, setup["agent"], retry={"enabled": True})
    talked = (await admin_client.post("/api/calls/outbound", json=_call(setup))).json()
    await _end(talked["id"], CallStatus.COMPLETED, outcome="Promised to pay")
    now = datetime.now(UTC)
    await _tick(now)
    await _tick(now + timedelta(days=1))
    assert len(_no_network) == 2


@pytest.mark.parametrize(
    ("text", "machine"),
    [
        ("The number you have dialled is currently switched off.", True),
        ("Please leave a message after the tone.", True),
        ("आप जिस नंबर पर कॉल कर रहे हैं वह अभी स्विच ऑफ है", True),
        ("ನೀವು ಕರೆ ಮಾಡಿದ ಸಂಖ್ಯೆ ಸ್ವಿಚ್ ಆಫ್ ಆಗಿದೆ", True),
        ("Hello, who is this?", False),
        ("I'm busy right now, call me later", False),
        ("Haan bolo, main abhi available nahi hoon", False),
    ],
)
def test_voicemail_phrases(text: str, machine: bool) -> None:
    assert is_voicemail(text) is machine


async def test_audit_log_exports_as_csv(admin_client: httpx.AsyncClient) -> None:
    await _branch(admin_client)
    resp = await admin_client.get("/api/audit-logs/export.csv")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0][:3] == ["time_utc", "who", "action"]
    assert any(r[2] == "branch.create" for r in rows[1:])
    logs = (await admin_client.get("/api/audit-logs")).json()["items"]
    assert logs[0]["action"] == "audit.export"


async def test_export_needs_audit_access(admin_client: httpx.AsyncClient) -> None:
    await admin_client.post(
        "/api/users",
        json={"email": "rm@yellowmetal.co", "name": "RM", "role": "viewer",
              "password": "viewer-password-1"},
    )  # fmt: skip
    await admin_client.post("/api/auth/logout")
    await admin_client.post(
        "/api/auth/login", json={"email": "rm@yellowmetal.co", "password": "viewer-password-1"}
    )
    assert (await admin_client.get("/api/audit-logs/export.csv")).status_code == 403
