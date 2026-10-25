import uuid
from typing import Any

import httpx
import pytest

from ymva.models import AIProvider
from ymva.redis import get_redis
from ymva.services import integrations as integrations_service
from ymva.telephony.base import PlacedCall
from ymva.telephony.plivo_family import PlivoClient


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Fake every outbound provider call; record placed calls."""
    placed: list[dict[str, Any]] = []

    async def verify_ai_key(provider: AIProvider, key: str) -> None:
        if key.startswith("bad"):
            raise integrations_service.VerificationError("The key was rejected.")

    async def verify(self: Any) -> None:
        return None

    async def place_call(self: Any, **kwargs: Any) -> PlacedCall:
        placed.append(kwargs)
        return PlacedCall(provider_call_id="req-1")

    monkeypatch.setattr(integrations_service, "verify_ai_key", verify_ai_key)
    monkeypatch.setattr(PlivoClient, "verify", verify)
    monkeypatch.setattr(PlivoClient, "place_call", place_call)
    return placed


async def _setup(client: httpx.AsyncClient, *, publish: bool = True) -> dict[str, Any]:
    await client.put("/api/integrations/ai/sarvam", json={"api_key": "sk-sarvam-12345678"})
    await client.put("/api/integrations/ai/google", json={"api_key": "AIza-google-12345678"})
    account = (
        await client.post(
            "/api/integrations/telephony",
            json={
                "provider": "plivo",
                "name": "Plivo main",
                "auth_id": "MA123456789",
                "auth_token": "tok",
            },
        )
    ).json()
    agent = (await client.post("/api/agents", json={"name": "Interest reminder"})).json()
    if publish:
        agent = (
            await client.post(
                f"/api/agents/{agent['id']}/publish",
                json={"version_id": agent["latest_version_id"]},
            )
        ).json()
    number = (
        await client.post(
            "/api/numbers",
            json={
                "e164": "080 1234 5678",
                "provider_account_id": account["id"],
                "inbound_agent_id": agent["id"],
            },
        )
    ).json()
    return {"account": account, "agent": agent, "number": number}


async def test_ai_keys_are_verified_encrypted_and_hidden(admin_client: httpx.AsyncClient) -> None:
    ok = (
        await admin_client.put(
            "/api/integrations/ai/google", json={"api_key": "AIza-google-12345678"}
        )
    ).json()
    assert ok["configured"] and ok["verified_at"] and ok["key_hint"] == "…5678"
    bad = (
        await admin_client.put("/api/integrations/ai/sarvam", json={"api_key": "bad-key-000000"})
    ).json()
    assert bad["verified_at"] is None and bad["last_error"] == "The key was rejected."
    listing = (await admin_client.get("/api/integrations")).json()
    assert "AIza-google-12345678" not in str(listing)
    assert {p["provider"] for p in listing["ai"]} == {"sarvam", "google"}


async def test_telephony_account_exposes_webhook_urls(admin_client: httpx.AsyncClient) -> None:
    data = await _setup(admin_client)
    account = data["account"]
    assert account["verified_at"] and account["credential_hint"] == "MA12…6789"
    assert (
        account["answer_url"].endswith("/answer")
        and f"/api/telephony/plivo/{account['id']}/" in account["answer_url"]
    )
    assert "tok" not in str(account)
    # Can't disconnect an account that still has numbers.
    assert (
        await admin_client.delete(f"/api/integrations/telephony/{account['id']}")
    ).status_code == 409


async def test_numbers_are_normalised_and_unique(admin_client: httpx.AsyncClient) -> None:
    data = await _setup(admin_client)
    assert data["number"]["e164"] == "+918012345678"
    assert data["number"]["inbound_agent_name"] == "Interest reminder"
    dup = await admin_client.post(
        "/api/numbers",
        json={"e164": "+91 80 1234 5678", "provider_account_id": data["account"]["id"]},
    )
    assert dup.status_code == 409
    bad = await admin_client.post(
        "/api/numbers", json={"e164": "123", "provider_account_id": data["account"]["id"]}
    )
    assert bad.status_code == 422


async def test_agent_versions_and_publishing(admin_client: httpx.AsyncClient) -> None:
    agent = (await admin_client.post("/api/agents", json={"name": "Reminder"})).json()
    assert agent["latest_version"] == 1 and agent["published_version"] is None
    assert agent["config"]["languages"] == ["en", "kn", "te", "hi", "ta"]

    config = agent["config"] | {"mode": "gemini_live"}
    saved = (
        await admin_client.post(
            f"/api/agents/{agent['id']}/versions", json={"config": config, "note": "try live"}
        )
    ).json()
    assert (
        saved["latest_version"] == 2
        and saved["mode"] == "gemini_live"
        and saved["has_unpublished_changes"]
    )

    invalid = config | {"default_language": "ml"}
    assert (
        await admin_client.post(f"/api/agents/{agent['id']}/versions", json={"config": invalid})
    ).status_code == 422

    published = (
        await admin_client.post(
            f"/api/agents/{agent['id']}/publish", json={"version_id": saved["latest_version_id"]}
        )
    ).json()
    assert published["published_version"] == 2 and published["status"] == "active"
    assert not published["has_unpublished_changes"]
    assert [v["version"] for v in published["versions"]] == [2, 1]


async def test_outbound_call_requires_published_agent(
    admin_client: httpx.AsyncClient, _no_network: list[dict[str, Any]]
) -> None:
    data = await _setup(admin_client, publish=False)
    body = {
        "agent_id": data["agent"]["id"],
        "from_number_id": data["number"]["id"],
        "to": "98765 43210",
    }
    refused = await admin_client.post("/api/calls/outbound", json=body)
    assert refused.status_code == 400 and "published" in refused.json()["detail"]

    await admin_client.post(
        f"/api/agents/{data['agent']['id']}/publish",
        json={"version_id": data["agent"]["latest_version_id"]},
    )
    body["variables"] = {"customer_name": "Ramesh", "due_amount": "2450"}
    call = (await admin_client.post("/api/calls/outbound", json=body)).json()
    assert call["status"] == "ringing" and call["to_number"] == "+919876543210"
    sent = _no_network[0]
    assert sent["answer_url"].endswith(f"/answer?call={call['id']}")
    assert sent["from_number"] == "+918012345678"

    detail = (await admin_client.get(f"/api/calls/{call['id']}")).json()
    assert detail["variables"] == {"customer_name": "Ramesh", "due_amount": "2450"}


async def test_answer_webhook_streams_audio_with_single_use_token(
    admin_client: httpx.AsyncClient,
) -> None:
    data = await _setup(admin_client)
    call = (
        await admin_client.post(
            "/api/calls/outbound",
            json={
                "agent_id": data["agent"]["id"],
                "from_number_id": data["number"]["id"],
                "to": "+919876543210",
            },
        )
    ).json()
    answer_url = data["account"]["answer_url"].split("/api/", 1)[1]

    wrong = await admin_client.post(f"/api/{answer_url.replace('/answer', 'x/answer')}", data={})
    assert wrong.status_code == 404

    resp = await admin_client.post(
        f"/api/{answer_url}?call={call['id']}", data={"CallUUID": "plivo-call-1"}
    )
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/xml")
    assert "<Stream" in resp.text and "/api/telephony/plivo/ws/" in resp.text
    token = resp.text.split("/api/telephony/plivo/ws/")[1].split("<")[0]
    assert await get_redis().get(f"ws-token:{token}") == call["id"]

    detail = (await admin_client.get(f"/api/calls/{call['id']}")).json()
    assert detail["status"] == "ringing"  # becomes in_progress when the audio stream starts


async def test_inbound_call_to_unassigned_number_is_declined(
    admin_client: httpx.AsyncClient,
) -> None:
    data = await _setup(admin_client)
    answer_url = data["account"]["answer_url"].split("/api/", 1)[1]
    resp = await admin_client.post(
        f"/api/{answer_url}",
        data={"CallUUID": "in-1", "From": "919999999999", "To": "910000000000"},
    )
    assert "<Hangup/>" in resp.text and "<Stream" not in resp.text

    ok = await admin_client.post(
        f"/api/{answer_url}",
        data={"CallUUID": "in-2", "From": "919999999999", "To": "918012345678"},
    )
    assert "<Stream" in ok.text
    calls = (await admin_client.get("/api/calls?direction=inbound")).json()
    assert calls["total"] == 1 and calls["items"][0]["from_number"] == "919999999999"


async def test_status_webhook_marks_unanswered_calls(admin_client: httpx.AsyncClient) -> None:
    data = await _setup(admin_client)
    call = (
        await admin_client.post(
            "/api/calls/outbound",
            json={
                "agent_id": data["agent"]["id"],
                "from_number_id": data["number"]["id"],
                "to": "+919876543210",
            },
        )
    ).json()
    status_url = data["account"]["status_url"].split("/api/", 1)[1]
    await admin_client.post(
        f"/api/{status_url}?call={call['id']}",
        data={"CallStatus": "no-answer", "HangupCause": "NO_ANSWER"},
    )
    detail = (await admin_client.get(f"/api/calls/{call['id']}")).json()
    assert detail["status"] == "no_answer" and detail["end_reason"] == "NO_ANSWER"


async def test_viewer_cannot_change_integrations_or_place_calls(
    admin_client: httpx.AsyncClient,
) -> None:
    await admin_client.post(
        "/api/users",
        json={
            "email": "ceo@yellowmetal.co",
            "name": "CEO",
            "role": "viewer",
            "password": "viewer-password-1",
        },
    )
    await admin_client.post("/api/auth/logout")
    await admin_client.post(
        "/api/auth/login", json={"email": "ceo@yellowmetal.co", "password": "viewer-password-1"}
    )
    assert (await admin_client.get("/api/integrations")).status_code == 200
    assert (
        await admin_client.put("/api/integrations/ai/google", json={"api_key": "AIza-12345678"})
    ).status_code == 403
    assert (await admin_client.post("/api/agents", json={"name": "x"})).status_code == 403
    outbound = {
        "agent_id": str(uuid.uuid4()),
        "from_number_id": str(uuid.uuid4()),
        "to": "+919876543210",
    }
    assert (await admin_client.post("/api/calls/outbound", json=outbound)).status_code == 403
