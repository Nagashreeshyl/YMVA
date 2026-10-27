from typing import Any

import httpx

from tests.test_calls_api import _no_network, _setup  # noqa: F401  (fixture used below)
from ymva.telephony.exotel import ExotelClient
from ymva.telephony.plivo_family import PlivoClient
from ymva.telephony.twilio_family import TwilioClient
from ymva.voice.config import Transfer


async def _agent_with_transfer(client: httpx.AsyncClient, phone: str) -> dict[str, Any]:
    setup = await _setup(client)
    aid = setup["agent"]["id"]
    config = setup["agent"]["config"]
    config["transfer"] = {"enabled": True, "phone": phone, "when": "they ask for a person"}
    await client.put(f"/api/agents/{aid}/draft", json={"config": config})
    published = (await client.post(f"/api/agents/{aid}/publish", json={})).json()
    assert published["config"]["transfer"]["enabled"]
    return setup


async def test_transfer_url_dials_the_agents_number(admin_client: httpx.AsyncClient) -> None:
    setup = await _agent_with_transfer(admin_client, "98450 11111")
    call = (
        await admin_client.post(
            "/api/calls/outbound",
            json={
                "agent_id": setup["agent"]["id"],
                "from_number_id": setup["number"]["id"],
                "to": "+919876543210",
            },
        )
    ).json()
    base = setup["account"]["answer_url"].split("/api/", 1)[1].removesuffix("/answer")
    resp = await admin_client.post(f"/api/{base}/transfer?call={call['id']}&to=+911")
    # The number comes from the agent, never the request; caller ID is our own number.
    assert '<Dial callerId="918012345678"><Number>919845011111</Number></Dial>' in resp.text
    wrong_secret = await admin_client.post(f"/api/{base}x/transfer?call={call['id']}")
    assert wrong_secret.status_code == 404


def test_transfer_number_needs_to_be_complete() -> None:
    assert Transfer(phone="+91 98450 11111").number == "+919845011111"
    assert Transfer(phone="98450 11111").number == "+919845011111"
    assert Transfer(phone="+91 984").number is None  # still being typed


def test_providers_build_a_transfer() -> None:
    twilio = TwilioClient({"account_sid": "AC1", "auth_token": "t"})
    body, _ = twilio.transfer_response("+919845011111", "+918012345678")
    assert '<Dial callerId="+918012345678"><Number>+919845011111</Number>' in body
    plivo, _ = PlivoClient({"auth_id": "MA1", "auth_token": "t"}).transfer_response(
        "+919845011111", "+918012345678"
    )
    assert "<Number>919845011111</Number>" in plivo


async def test_exotel_cannot_transfer() -> None:
    import pytest

    from ymva.telephony import TelephonyError

    client = ExotelClient({})
    with pytest.raises(TelephonyError, match="can't transfer"):
        await client.transfer("c", url="u", to_number="+91", caller_id="+91")
