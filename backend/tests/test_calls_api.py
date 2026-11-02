import uuid
from typing import Any

import httpx
import pytest

from ymva.models import AIProvider
from ymva.redis import get_redis
from ymva.services import integrations as integrations_service
from ymva.services import outbound as outbound_service
from ymva.telephony.base import PlacedCall
from ymva.telephony.plivo_family import PlivoClient
from ymva.voice import greeting


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
        return PlacedCall(provider_call_id=f"req-{len(placed)}")  # unique, like real providers

    async def no_greeting(*_: Any) -> None:
        return None

    monkeypatch.setattr(greeting, "_synthesize", no_greeting)
    # The test address isn't on the internet; treat it as just checked and reachable.
    monkeypatch.setattr(outbound_service, "_reachable_at", float("inf"))
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

    config = agent["config"] | {"mode": "pipeline"}
    saved = (
        await admin_client.post(
            f"/api/agents/{agent['id']}/versions", json={"config": config, "note": "try live"}
        )
    ).json()
    assert (
        saved["latest_version"] == 2
        and saved["mode"] == "pipeline"
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

    # Without a public address the provider can't reach us: say so plainly.
    from ymva.config import get_settings

    settings = get_settings()
    saved = settings.public_url, settings.telephony_public_url
    settings.public_url, settings.telephony_public_url = "http://localhost", ""
    try:
        local = await admin_client.post("/api/calls/outbound", json=body)
    finally:
        settings.public_url, settings.telephony_public_url = saved
    assert local.status_code == 400 and "--tunnel" in local.json()["detail"]


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
    assert (await admin_client.get("/api/integrations")).status_code == 403
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


async def test_editor_saves_drafts_without_creating_versions(
    admin_client: httpx.AsyncClient,
) -> None:
    agent = (await admin_client.post("/api/agents", json={"name": "Drafts"})).json()
    aid = agent["id"]
    config = agent["config"] | {"mode": "pipeline"}

    for temperature in (0.3, 0.5, 0.7):  # several edits in a row
        config["pipeline"]["temperature"] = temperature
        draft = (await admin_client.put(f"/api/agents/{aid}/draft", json={"config": config})).json()
    assert draft["latest_version"] == 1 and draft["has_draft_changes"]
    assert (
        draft["config"]["mode"] == "pipeline" and draft["config"]["pipeline"]["temperature"] == 0.7
    )

    saved = (
        await admin_client.post(f"/api/agents/{aid}/versions", json={"note": "try live"})
    ).json()
    assert saved["latest_version"] == 2 and not saved["has_draft_changes"]
    again = await admin_client.post(f"/api/agents/{aid}/versions", json={"note": "same"})
    assert again.status_code == 409

    # Restore v1 into the editor, then publish straight from the draft.
    v1 = next(v for v in saved["versions"] if v["version"] == 1)
    restored = (await admin_client.post(f"/api/agents/{aid}/versions/{v1['id']}/restore")).json()
    assert restored["config"]["mode"] == "gemini_live" and restored["has_draft_changes"]
    published = (await admin_client.post(f"/api/agents/{aid}/publish", json={})).json()
    assert published["latest_version"] == 3 and published["published_version"] == 3
    assert not published["has_unpublished_changes"]

    discarded = (await admin_client.delete(f"/api/agents/{aid}/draft")).json()
    assert discarded["config"]["mode"] == "gemini_live"


async def test_retired_models_are_upgraded(admin_client: httpx.AsyncClient) -> None:
    agent = (await admin_client.post("/api/agents", json={"name": "Old model"})).json()
    config = agent["config"]
    config["pipeline"]["llm_model"] = "gemini-2.5-flash"
    draft = (
        await admin_client.put(f"/api/agents/{agent['id']}/draft", json={"config": config})
    ).json()
    assert draft["config"]["pipeline"]["llm_model"] == "gemini-3.5-flash-lite"


async def test_archive_then_delete_keeps_call_history(admin_client: httpx.AsyncClient) -> None:
    data = await _setup(admin_client)
    aid = data["agent"]["id"]
    call = (
        await admin_client.post(
            "/api/calls/outbound",
            json={"agent_id": aid, "from_number_id": data["number"]["id"], "to": "+919876543210"},
        )
    ).json()
    assert (await admin_client.delete(f"/api/agents/{aid}")).status_code == 400  # archive first

    await admin_client.patch(f"/api/agents/{aid}", json={"status": "archived"})
    assert (await admin_client.get("/api/agents")).json() == []
    assert [a["id"] for a in (await admin_client.get("/api/agents?archived=true")).json()] == [aid]
    number = (await admin_client.get("/api/numbers")).json()[0]
    assert number["inbound_agent_id"] is None  # archived agents stop answering

    assert (await admin_client.delete(f"/api/agents/{aid}")).status_code == 204
    assert (await admin_client.get(f"/api/agents/{aid}")).status_code == 404
    kept = (await admin_client.get(f"/api/calls/{call['id']}")).json()
    assert kept["agent_name"] == "Interest reminder"


async def test_voice_preview_is_cached(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await admin_client.put("/api/integrations/ai/sarvam", json={"api_key": "sk-sarvam-12345678"})
    calls: list[str] = []

    async def fake_sarvam(
        text: str, language: str, voice: str, pace: float, rate: int, key: str
    ) -> tuple[bytes, int]:
        calls.append(text)
        return b"\x00\x00" * 2400, 24000

    monkeypatch.setattr(greeting, "synthesize_sarvam", fake_sarvam)
    body = {"engine": "sarvam", "voice": "kavya", "language": "kn"}
    first = await admin_client.post("/api/agents/voice-preview", json=body)
    second = await admin_client.post("/api/agents/voice-preview", json=body)
    assert first.status_code == 200 and first.headers["content-type"] == "audio/wav"
    assert first.content[:4] == b"RIFF" and second.content == first.content
    assert len(calls) == 1 and "Kavya" in calls[0]
    bad = await admin_client.post("/api/agents/voice-preview", json={**body, "voice": "nobody"})
    assert bad.status_code == 422


async def test_repeated_inbound_answer_webhook_reuses_the_call(
    admin_client: httpx.AsyncClient,
) -> None:
    setup = await _setup(admin_client)
    url = setup["account"]["answer_url"]
    form = {"CallUUID": "abc-123", "From": "919845012345", "To": "918012345678"}
    first = await admin_client.post(url, data=form)
    again = await admin_client.post(url, data=form)  # provider retry after a slow reply
    assert first.status_code == again.status_code == 200
    assert "<Stream" in again.text
    calls = (await admin_client.get("/api/calls")).json()
    assert calls["total"] == 1


async def test_phone_calls_keep_the_settings_they_ran_with(
    admin_client: httpx.AsyncClient, _no_network: list[dict[str, Any]]
) -> None:
    from ymva.db import get_sessionmaker
    from ymva.models import Call
    from ymva.services.analysis import _analysis

    data = await _setup(admin_client)
    aid = data["agent"]["id"]
    draft = data["agent"]["config"]
    draft["analysis"]["details"] = [{"name": "Appointment date", "description": "YYYY-MM-DD"}]
    await admin_client.put(f"/api/agents/{aid}/draft", json={"config": draft})
    body = {"agent_id": aid, "from_number_id": data["number"]["id"], "to": "98765 43210"}

    published = (await admin_client.post("/api/calls/outbound", json=body)).json()
    edits = (
        await admin_client.post("/api/calls/outbound", json={**body, "use_edits": True})
    ).json()
    async with get_sessionmaker()() as db:
        live = await db.get(Call, uuid.UUID(published["id"]))
        tried = await db.get(Call, uuid.UUID(edits["id"]))
        assert live is not None and tried is not None
        assert [d.name for d in (await _analysis(db, tried)).details] == ["Appointment date"]
        assert "Appointment date" not in [d.name for d in (await _analysis(db, live)).details]
        # Calls from before settings were kept on the call fall back to their version.
        live.agent_config = None
        assert (await _analysis(db, live)).details  # not empty defaults from nowhere


async def test_an_expired_public_address_is_explained_before_dialing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ymva.services.calls import CallSetupError

    monkeypatch.setattr(outbound_service, "_reachable_at", 0.0)
    monkeypatch.setattr(
        outbound_service, "public_base_url", lambda: "https://gone.trycloudflare.com"
    )

    async def unreachable(*_: Any, **__: Any) -> Any:
        raise httpx.ConnectError("Tunnel not found")

    monkeypatch.setattr(httpx.AsyncClient, "get", unreachable)
    with pytest.raises(CallSetupError, match="--tunnel"):
        await outbound_service._check_public_address("Vobiz")
