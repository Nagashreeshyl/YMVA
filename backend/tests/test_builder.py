from typing import Any

import httpx
import pytest

from ymva.services import builder
from ymva.services import integrations as integrations_service
from ymva.services.builder import AgentSpec, AssistantResult, from_spec, to_spec
from ymva.voice.config import AgentConfig
from ymva.voice.templates import blank, interest_reminder


def _spec(**overrides: Any) -> AgentSpec:
    spec = to_spec(interest_reminder(), "Reminder", "Reminds customers")
    return spec.model_copy(update=overrides)


@pytest.fixture(autouse=True)
def _fake_gemini(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Replace Gemini with queued answers; record what it was asked."""
    answers: list[Any] = []
    asked: list[Any] = []

    async def generate(api_key: str, system: str, contents: list[Any], schema: type) -> Any:
        asked.append(contents)
        return answers.pop(0)

    async def verify_ai_key(*_: Any) -> None:
        return None

    monkeypatch.setattr(builder, "ask_gemini", generate)
    monkeypatch.setattr(integrations_service, "verify_ai_key", verify_ai_key)
    return answers


async def _with_key(client: httpx.AsyncClient) -> None:
    resp = await client.put("/api/integrations/ai/google", json={"api_key": "AIza-google-12345678"})
    assert resp.status_code == 200, resp.text


def test_spec_round_trip_keeps_the_agent() -> None:
    config = interest_reminder()
    assert from_spec(to_spec(config, "n", "d"), config) == config


def test_from_spec_rejects_greetings_in_other_languages() -> None:
    from pydantic import ValidationError

    spec = _spec(languages=["en"], default_language="en")
    with pytest.raises(ValidationError):
        from_spec(spec, blank())


async def test_generate_creates_agent_and_starts_chat(
    admin_client: httpx.AsyncClient, _fake_gemini: list[Any]
) -> None:
    await _with_key(admin_client)
    _fake_gemini.append(_spec(name="EMI reminder", description="Reminds about EMIs"))
    resp = await admin_client.post(
        "/api/agents/generate", json={"description": "Call customers about their EMI due date"}
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    assert agent["name"] == "EMI reminder"
    assert agent["versions"][0]["source"] == "ai"
    history = (await admin_client.get(f"/api/agents/{agent['id']}/assistant")).json()
    assert [m["role"] for m in history] == ["user", "assistant"]


async def test_generate_retries_an_invalid_agent(
    admin_client: httpx.AsyncClient, _fake_gemini: list[Any]
) -> None:
    await _with_key(admin_client)
    _fake_gemini.extend([_spec(default_language="xx"), _spec()])
    resp = await admin_client.post("/api/agents/generate", json={"description": "A reminder agent"})
    assert resp.status_code == 201, resp.text


async def test_generate_needs_google_key(admin_client: httpx.AsyncClient) -> None:
    resp = await admin_client.post("/api/agents/generate", json={"description": "A reminder agent"})
    assert resp.status_code == 400


async def test_assistant_edits_draft_and_undo_restores_it(
    admin_client: httpx.AsyncClient, _fake_gemini: list[Any]
) -> None:
    await _with_key(admin_client)
    agent = (await admin_client.post("/api/agents", json={"name": "Reminder"})).json()
    original = agent["config"]

    edited = to_spec(AgentConfig.model_validate(original), "Reminder", "")
    edited = edited.model_copy(update={"pipeline_voice": "rahul"})
    _fake_gemini.append(
        AssistantResult(reply="Switched to Rahul.", changes=["Voice: Rahul"], agent=edited)
    )
    resp = await admin_client.post(
        f"/api/agents/{agent['id']}/assistant", json={"message": "Use a male voice"}
    )
    assert resp.status_code == 200, resp.text
    turn = resp.json()
    assert turn["agent"]["config"]["pipeline"]["voice"] == "rahul"
    assert turn["agent"]["has_draft_changes"]
    assert len(turn["agent"]["versions"]) == 1  # edits are drafts, not versions
    change = turn["messages"][1]
    assert change["can_undo"] and change["changes"] == ["Voice: Rahul"]

    resp = await admin_client.post(f"/api/agents/{agent['id']}/assistant/{change['id']}/undo")
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"] == original
    history = (await admin_client.get(f"/api/agents/{agent['id']}/assistant")).json()
    assert history[-1]["undone"] and not history[-1]["can_undo"]


async def test_assistant_answer_without_change_leaves_draft(
    admin_client: httpx.AsyncClient, _fake_gemini: list[Any]
) -> None:
    await _with_key(admin_client)
    agent = (await admin_client.post("/api/agents", json={"name": "Reminder"})).json()
    _fake_gemini.append(AssistantResult(reply="It speaks five languages.", changes=[], agent=None))
    resp = await admin_client.post(
        f"/api/agents/{agent['id']}/assistant", json={"message": "Which languages?"}
    )
    assert resp.status_code == 200, resp.text
    assert not resp.json()["agent"]["has_draft_changes"]
    assert not resp.json()["messages"][1]["can_undo"]


async def test_undo_refused_after_manual_edit(
    admin_client: httpx.AsyncClient, _fake_gemini: list[Any]
) -> None:
    await _with_key(admin_client)
    agent = (await admin_client.post("/api/agents", json={"name": "Reminder"})).json()
    edited = to_spec(AgentConfig.model_validate(agent["config"]), "Reminder", "")
    _fake_gemini.append(
        AssistantResult(
            reply="Done.", changes=["Pace"], agent=edited.model_copy(update={"mode": "gemini_live"})
        )
    )
    turn = (
        await admin_client.post(f"/api/agents/{agent['id']}/assistant", json={"message": "Live"})
    ).json()
    config = turn["agent"]["config"]
    config["system_prompt"] += " Be extra polite."
    await admin_client.put(f"/api/agents/{agent['id']}/draft", json={"config": config})
    resp = await admin_client.post(
        f"/api/agents/{agent['id']}/assistant/{turn['messages'][1]['id']}/undo"
    )
    assert resp.status_code == 409
