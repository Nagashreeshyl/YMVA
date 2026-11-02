"""Other apps (n8n, CRMs) start calls with an API key and get the result back."""

from typing import Any

import httpx
import pytest

from tests.test_calls_api import _no_network, _setup  # noqa: F401  (fixture used below)
from ymva.api.routes import calls as calls_routes
from ymva.db import get_sessionmaker
from ymva.models import Call, CallStatus, PhoneNumber, ProviderAccount, TelephonyProvider
from ymva.services import callback


async def test_app_starts_a_call_with_a_caller_key(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def allowed(url: str) -> None:
        return None

    monkeypatch.setattr(calls_routes, "check_destination", allowed)
    c = admin_client
    setup = await _setup(c)
    made = await c.post("/api/api-keys", json={"name": "n8n", "role": "caller"})
    assert made.status_code == 201, made.text
    key = made.json()["key"]
    assert key.startswith("ymva_") and "key" not in (await c.get("/api/api-keys")).json()[0]

    app = httpx.AsyncClient(
        transport=c._transport, base_url=str(c.base_url), headers={"authorization": f"Bearer {key}"}
    )
    me = await app.get("/api/auth/me")  # n8n's credential test
    assert me.status_code == 200 and me.json()["role"] == "caller", me.text
    agents = (await app.get("/api/agents")).json()
    assert [a["id"] for a in agents] == [setup["agent"]["id"]]
    # A leftover simulator number must not make the choice ambiguous.
    async with get_sessionmaker()() as db:
        sim = ProviderAccount(
            provider=TelephonyProvider.SIMULATOR,
            name="Simulator",
            encrypted_credentials="x",
            webhook_secret="s",
        )
        db.add(sim)
        await db.flush()
        db.add(PhoneNumber(provider_account_id=sim.id, e164="+918036446890", label="Simulator"))
        await db.commit()
    # No from number: the only real active one is used.
    started = await app.post(
        "/api/calls/outbound",
        json={
            "agent_id": setup["agent"]["id"],
            "to": "98450 11111",
            "variables": {"customer_name": "Ramesh"},
            "callback_url": "https://hooks.example.com/ymva",
        },
    )
    assert started.status_code == 201, started.text
    call_id = started.json()["id"]
    assert (await app.get(f"/api/calls/{call_id}")).json()["status"] == "ringing"
    assert (
        await app.patch(f"/api/agents/{setup['agent']['id']}", json={"name": "x"})
    ).status_code == 403

    revoked = await c.delete(f"/api/api-keys/{made.json()['id']}")
    assert revoked.status_code == 204
    assert (await app.get("/api/agents")).status_code == 401


async def test_result_is_posted_to_the_callback(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, dict[str, Any]]] = []

    async def post(self: Any, url: str, json: dict[str, Any]) -> httpx.Response:
        sent.append((url, json))
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    async with get_sessionmaker()() as db:
        call = Call(
            direction="outbound", status=CallStatus.COMPLETED, to_number="+919845011111",
            callback_url="https://hooks.example.com/ymva", outcome="Promised to pay",
            summary="Will pay on the 20th.", extracted={"Amount": "5000"},
        )  # fmt: skip
        db.add(call)
        await db.commit()
    await callback.send_callback(call.id)
    assert sent and sent[0][0] == "https://hooks.example.com/ymva"
    body = sent[0][1]
    assert body["event"] == "call.ended" and body["outcome"] == "Promised to pay"
    assert body["details"] == {"Amount": "5000"} and body["status"] == "completed"
