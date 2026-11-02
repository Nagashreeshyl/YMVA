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
            reply="Done.", changes=["Pace"], agent=edited.model_copy(update={"mode": "pipeline"})
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


def test_assistant_can_change_after_call_outcomes_and_details() -> None:
    from ymva.services.builder import TrackedSpec

    spec = _spec(
        outcomes=[TrackedSpec(name="Appointment booked", description="Agreed a slot")],
        details=[TrackedSpec(name="Appointment date", description="As YYYY-MM-DD")],
    )
    config = from_spec(spec, interest_reminder())
    assert [o.name for o in config.analysis.outcomes] == ["Appointment booked"]
    assert [d.name for d in config.analysis.details] == ["Appointment date"]


async def test_agent_is_built_from_a_talked_through_conversation(
    admin_client: httpx.AsyncClient, _fake_gemini: list[Any]
) -> None:
    from ymva.db import get_sessionmaker
    from ymva.models import Call, CallDirection, CallEvent, CallEventType, CallStatus

    await _with_key(admin_client)
    me = (await admin_client.get("/api/auth/me")).json()
    async with get_sessionmaker()() as db:
        call = Call(
            agent_name=builder.INTERVIEWER_NAME,
            direction=CallDirection.WEB,
            status=CallStatus.COMPLETED,
            placed_by_id=me["id"],
            agent_config=builder.interviewer().model_dump(mode="json"),
        )
        db.add(call)
        await db.flush()
        turns = [
            (CallEventType.AGENT_TURN, "Tell me about the agent you want to create."),
            (CallEventType.USER_TURN, "It reminds customers about EMIs. Never be rude."),
        ]
        for seq, (kind, text) in enumerate(turns):
            db.add(CallEvent(call_id=call.id, seq=seq, type=kind, content=text))
        await db.commit()
        call_id = str(call.id)

    _fake_gemini.append(_spec(name="EMI reminder"))
    resp = await admin_client.post("/api/agents/generate", json={"call_id": call_id})
    assert resp.status_code == 201, resp.text
    history = (await admin_client.get(f"/api/agents/{resp.json()['id']}/assistant")).json()
    assert "Admin: It reminds customers about EMIs. Never be rude." in history[0]["content"]
    assert "Interviewer: Tell me about" in history[0]["content"]

    # Nothing to build from: neither a description nor a conversation.
    assert (await admin_client.post("/api/agents/generate", json={})).status_code == 400


async def test_yma_creates_a_tool_and_asks_for_its_key_securely(
    admin_client: httpx.AsyncClient, _fake_gemini: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_tools import SERVICE_ACCOUNT
    from ymva.services import tool_builder
    from ymva.services.builder import ToolRequest
    from ymva.services.tool_builder import GeneratedTool

    monkeypatch.setattr(tool_builder, "ask_gemini", builder.ask_gemini)  # same fake queue
    c = admin_client
    await _with_key(c)
    agent = (
        await c.post("/api/agents", json={"name": "Reminder", "template": "interest_reminder"})
    ).json()

    def sheet_tool(link: str = "") -> GeneratedTool:
        return GeneratedTool(
            name="save_payment_promise",
            description="Use when the customer says when they will pay.",
            type="google_sheets",
            params=[{"name": "promised_date", "description": "YYYY-MM-DD", "required": True}],
            sheet_action="append_row",
            sheet_columns=[{"header": "Promised date", "value": "{{promised_date}}"}],
            sheet_match_column="",
            sheet_match_value="",
            sheet_link=link,
            http_method="POST",
            http_url="",
            http_body="",
            db_query="",
            db_mode="read",
        )

    ask = "Save the date they promise to pay in our Google Sheet."
    _fake_gemini.extend(
        [
            AssistantResult(
                reply="I'll save promised dates to your sheet.",
                changes=[],
                agent=None,
                tools=[ToolRequest(name="save_payment_promise", request=ask)],
            ),
            sheet_tool(),
        ]
    )
    turn = (await c.post(f"/api/agents/{agent['id']}/assistant", json={"message": ask})).json()
    answer = turn["messages"][1]
    assert answer["changes"] == ["Tool created: save_payment_promise"]
    assert (
        "paste the Google Sheet's link" in answer["content"] and "secure field" in answer["content"]
    )
    assert "save_payment_promise" in turn["agent"]["config"]["tools"]
    tool = (await c.get("/api/tools")).json()[0]
    assert not tool["is_active"] and tool["missing"] == ["link", "key"]  # off until complete

    # The key goes straight to the tool, never into the chat.
    keyed = await c.put(
        f"/api/tools/{tool['id']}/secrets", json={"secrets": {"service_account": SERVICE_ACCOUNT}}
    )
    assert keyed.status_code == 200, keyed.text
    assert keyed.json()["missing"] == ["link"] and not keyed.json()["is_active"]
    history = (await c.get(f"/api/agents/{agent['id']}/assistant")).json()
    assert all(SERVICE_ACCOUNT["private_key"] not in m["content"] for m in history)

    # The link typed in the chat finishes it, and it switches on.
    link = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit"
    _fake_gemini.extend(
        [
            AssistantResult(
                reply="Added the link.",
                changes=[],
                agent=None,
                tools=[ToolRequest(name="save_payment_promise", request=f"Sheet link: {link}")],
            ),
            sheet_tool(link),
        ]
    )
    turn = (await c.post(f"/api/agents/{agent['id']}/assistant", json={"message": link})).json()
    assert turn["messages"][1]["changes"] == ["Tool updated: save_payment_promise"]
    tool = (await c.get("/api/tools")).json()[0]
    assert tool["is_active"] and tool["missing"] == [] and tool["has_secrets"]


async def test_yma_removes_unfinished_tools_and_runs_tests(
    admin_client: httpx.AsyncClient, _fake_gemini: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from ymva.api.routes import builder as builder_route
    from ymva.services.builder import TestValue, ToolTest

    c = admin_client
    await _with_key(c)
    agent = (await c.post("/api/agents", json={"name": "Reminder"})).json()
    telegram = {
        "name": "send_telegram_message",
        "description": "Send the call summary to the team on Telegram.",
        "type": "http",
        "config": {
            "method": "POST",
            "url": "https://api.telegram.org/bot{{secret.bot_token}}/sendMessage",
            "body": '{"chat_id": "{{secret.chat_id}}", "text": "{{summary}}"}',
        },
        "agent_id": agent["id"],
    }
    made = (await c.post("/api/tools", json=telegram)).json()
    assert made["missing"] == ["key"]  # both secrets still to give
    unfinished = {
        **telegram,
        "name": "save_customer_details_sheet",
        "config": {"method": "POST", "url": "https://example.com"},
    }
    await c.post("/api/tools", json=unfinished)

    async def ran(*_: Any) -> dict[str, Any]:
        return {"ok": True}

    monkeypatch.setattr(builder_route, "run_tool", ran)
    # Not ready yet: YMA says what's missing instead of pretending it worked.
    _fake_gemini.append(
        AssistantResult(
            reply="Sending a test.",
            changes=[],
            agent=None,
            test_tool=ToolTest(name="send_telegram_message"),
        )
    )
    turn = (
        await c.post(f"/api/agents/{agent['id']}/assistant", json={"message": "send me a test"})
    ).json()
    assert "can't test send_telegram_message yet" in turn["messages"][1]["content"]

    keyed = await c.put(
        f"/api/tools/{made['id']}/secrets",
        json={"secrets": {"values": {"bot_token": "123:abc", "chat_id": "987"}}},
    )
    assert keyed.json()["missing"] == [] and keyed.json()["is_active"]
    _fake_gemini.append(
        AssistantResult(
            reply="Sending a test.",
            changes=[],
            agent=None,
            test_tool=ToolTest(
                name="send_telegram_message", values=[TestValue(name="summary", value="Test")]
            ),
            remove_tools=["save_customer_details_sheet"],
        )
    )
    turn = (
        await c.post(f"/api/agents/{agent['id']}/assistant", json={"message": "no sheets; test it"})
    ).json()
    answer = turn["messages"][1]
    assert "Test: I ran send_telegram_message and it worked." in answer["content"]
    assert answer["changes"] == ["Tool removed: save_customer_details_sheet"]
    assert turn["agent"]["config"]["tools"] == ["send_telegram_message"]
    names = [t["name"] for t in (await c.get("/api/tools")).json()]
    assert names == ["send_telegram_message"]  # the never-finished sheet tool is gone


async def test_yma_attaches_result_senders_after_the_call(
    admin_client: httpx.AsyncClient, _fake_gemini: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from ymva.services import tool_builder
    from ymva.services.builder import ToolRequest
    from ymva.services.tool_builder import GeneratedTool

    monkeypatch.setattr(tool_builder, "ask_gemini", builder.ask_gemini)
    c = admin_client
    await _with_key(c)
    agent = (await c.post("/api/agents", json={"name": "Reminder"})).json()
    ask = "Send the summary of what the customer told to my Telegram"
    _fake_gemini.extend(
        [
            AssistantResult(
                reply="I'll send each call's summary to Telegram.",
                changes=[],
                agent=None,
                tools=[ToolRequest(name="send_telegram_summary", when="after_call", request=ask)],
            ),
            GeneratedTool(
                name="send_telegram_summary",
                description="Sends the call summary to Telegram.",
                type="http",
                params=[],
                sheet_action="append_row",
                sheet_columns=[],
                sheet_match_column="",
                sheet_match_value="",
                http_method="POST",
                http_url="https://api.telegram.org/bot{{secret.bot_token}}/sendMessage",
                http_body='{"chat_id": "{{secret.chat_id}}", "text": "{{summary}}"}',
                db_query="",
                db_mode="read",
                needs_key=True,
            ),
        ]
    )
    turn = (await c.post(f"/api/agents/{agent['id']}/assistant", json={"message": ask})).json()
    config = turn["agent"]["config"]
    assert config["analysis"]["send_to"] == ["send_telegram_summary"]
    assert "send_telegram_summary" not in config["tools"]  # runs once the call ends
    assert "secure field" in turn["messages"][1]["content"]
