import uuid
from typing import Any

import httpx
import pytest

from ymva.db import get_sessionmaker
from ymva.models import Call, CallDirection, CallEvent, CallEventType, CallStatus
from ymva.services import analysis
from ymva.services import integrations as integrations_service
from ymva.voice.templates import interest_reminder


@pytest.fixture(autouse=True)
def _fake_gemini(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Gemini answers as if the customer promised to pay; records the prompt."""
    prompts: list[str] = []

    async def ask(api_key: str, system: str, contents: list[Any], schema: type) -> Any:
        prompts.append(contents[0].parts[0].text)
        return schema.model_validate(
            {
                "summary": "Ramesh will pay on the 20th.",
                "outcome": "Promised to pay",
                "details": [
                    {"name": "Promised payment date", "value": "2026-11-20"},
                    {"name": "Call back at", "value": "  "},
                ],
            }
        )

    async def verify_ai_key(*_: Any) -> None:
        return None

    monkeypatch.setattr(analysis, "ask_gemini", ask)
    monkeypatch.setattr(integrations_service, "verify_ai_key", verify_ai_key)
    return prompts


async def _call(turns: list[tuple[CallEventType, str]]) -> uuid.UUID:
    async with get_sessionmaker()() as db:
        call = Call(
            direction=CallDirection.WEB,
            status=CallStatus.COMPLETED,
            agent_config=interest_reminder().model_dump(mode="json"),
            variables={"customer_name": "Ramesh"},
        )
        db.add(call)
        await db.flush()
        for seq, (kind, text) in enumerate(turns):
            db.add(CallEvent(call_id=call.id, seq=seq, type=kind, content=text))
        await db.commit()
        return call.id


async def test_call_is_summarised_with_its_agents_outcomes(
    admin_client: httpx.AsyncClient, _fake_gemini: list[str]
) -> None:
    await admin_client.put("/api/integrations/ai/google", json={"api_key": "AIza-google-12345678"})
    call_id = await _call(
        [
            (CallEventType.AGENT_TURN, "Is this Ramesh?"),
            (CallEventType.USER_TURN, "Yes, I'll pay on the 20th."),
        ]
    )
    resp = await admin_client.post(f"/api/calls/{call_id}/analyze")
    assert resp.status_code == 200, resp.text
    call = resp.json()
    assert call["outcome"] == "Promised to pay"
    assert call["summary"] == "Ramesh will pay on the 20th."
    assert call["extracted"] == {"Promised payment date": "2026-11-20"}  # blank values dropped
    assert "- Already paid:" in _fake_gemini[0] and "Customer: Yes" in _fake_gemini[0]

    listed = await admin_client.get("/api/calls", params={"outcome": "Promised to pay"})
    assert [c["id"] for c in listed.json()["items"]] == [str(call_id)]
    assert (await admin_client.get("/api/calls", params={"outcome": "Refused"})).json()[
        "total"
    ] == 0


async def test_call_without_customer_speech_needs_no_ai(
    admin_client: httpx.AsyncClient, _fake_gemini: list[str]
) -> None:
    call_id = await _call([(CallEventType.AGENT_TURN, "Hello?")])
    await analysis.analyze_call(call_id)
    async with get_sessionmaker()() as db:
        call = await db.get(Call, call_id)
        assert call is not None and call.outcome == analysis.NO_CONVERSATION
    assert _fake_gemini == []


def test_cost_estimate_per_engine() -> None:
    from ymva.voice.pricing import estimate

    live = estimate(
        "gemini_live",
        "",
        {
            "llm_input_audio_tokens": 1e6,
            "llm_prompt_tokens": 1e6,
            "llm_completion_tokens": 1e6,
            "llm_output_audio_tokens": 1e6,
        },
    )
    assert live["total"] == 15.0  # $3 audio in + $12 audio out per 1M
    standard = estimate(
        "pipeline", "gemini-3.5-flash-lite", {"llm_prompt_tokens": 1e6, "stt_seconds": 60}
    )
    assert standard["gemini"] == 0.3 and standard["sarvam_stt"] > 0
