"""Twilio, Telnyx, Exotel and Vonage: what each sends to its API and how its webhooks and media
streams are read. Provider APIs are faked; request shapes follow each provider's docs."""

import json
from typing import Any
from urllib.parse import parse_qs
from xml.etree import ElementTree

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ymva.telephony import exotel, twilio_family, vonage
from ymva.telephony.base import StreamStart, TelephonyError
from ymva.telephony.exotel import ExotelClient
from ymva.telephony.twilio_family import TelnyxClient, TwilioClient
from ymva.telephony.vonage import VonageClient

ANSWER, STATUS, WS = "https://y/answer?call=1", "https://y/status?call=1", "wss://y/ws/tok"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Requests sent to providers; each answers with a plausible success body."""
    seen: list[httpx.Request] = []
    replies = {
        "api.twilio.com": {"sid": "CA123"},
        "api.telnyx.com": {"data": {"call_sid": "v3:abc"}},
        "api.in.exotel.com": {"Call": {"Sid": "ex-sid-1"}},
        "api.nexmo.com": {"uuid": "vg-uuid-1"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=replies.get(request.url.host, {}))

    real = httpx.AsyncClient
    for module in (twilio_family, exotel, vonage):
        monkeypatch.setattr(
            module.httpx,
            "AsyncClient",
            lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k),
        )
    return seen


async def _place(client: Any) -> Any:
    return await client.place_call(
        from_number="+918012345678",
        to_number="+919845012345",
        answer_url=ANSWER,
        status_url=STATUS,
        time_limit=630,
    )


async def test_twilio(api: list[httpx.Request]) -> None:
    client = TwilioClient(
        TwilioClient.validate_credentials({"account_sid": "AC1", "auth_token": "t"})
    )
    assert (await _place(client)).provider_call_id == "CA123"
    sent = api[-1]
    assert sent.url.path == "/2010-04-01/Accounts/AC1/Calls.json"
    form = parse_qs(sent.content.decode())
    assert form["To"] == ["+919845012345"] and form["Url"] == [ANSWER]
    assert form["StatusCallback"] == [STATUS] and form["TimeLimit"] == ["630"]
    stream = ElementTree.fromstring(client.answer_response(WS, STATUS)[0].split("\n", 1)[1])
    assert stream.find("Connect/Stream").get("url") == WS  # type: ignore[union-attr]
    hook = client.parse_webhook({"CallSid": "CA123", "CallStatus": "no-answer"})
    assert (hook.provider_call_id, hook.status) == ("CA123", "no_answer")
    assert client.parse_stream_start({"event": "connected"}) is None
    start = client.parse_stream_start(
        {"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA123"}}
    )
    assert start == StreamStart("MZ1", "CA123")
    client.serializer(start)  # builds with the account's credentials


async def test_telnyx(api: list[httpx.Request]) -> None:
    client = TelnyxClient({"api_key": "KEY1", "texml_app_id": "app9"})
    assert (await _place(client)).provider_call_id == "v3:abc"
    sent = api[-1]
    assert sent.url.path == "/v2/texml/calls/app9"
    assert sent.headers["Authorization"] == "Bearer KEY1"
    assert json.loads(sent.content)["Url"] == ANSWER
    xml = client.answer_response(WS, STATUS)[0]
    stream = ElementTree.fromstring(xml.split("\n", 1)[1]).find("Connect/Stream")
    assert stream is not None and stream.get("bidirectionalMode") == "rtp"
    start = client.parse_stream_start(
        {"event": "start", "stream_id": "s1", "start": {"call_control_id": "cc1"}}
    )
    assert start == StreamStart("s1", "cc1")
    client.serializer(start)


async def test_exotel(api: list[httpx.Request]) -> None:
    creds = {
        "api_key": "k",
        "api_token": "t",
        "account_sid": "yellowmetal1",
        "subdomain": "api.in.exotel.com",
        "app_id": "12345",
    }
    client = ExotelClient(ExotelClient.validate_credentials(creds))
    assert (await _place(client)).provider_call_id == "ex-sid-1"
    form = parse_qs(api[-1].content.decode())
    assert api[-1].url.path == "/v1/Accounts/yellowmetal1/Calls/connect.json"
    # Exotel dials "From" first: the customer; "CallerId" is our ExoPhone.
    assert form["From"] == ["+919845012345"] and form["CallerId"] == ["+918012345678"]
    assert form["Url"] == ["http://my.exotel.com/yellowmetal1/exoml/start_voice/12345"]
    assert json.loads(client.answer_response(WS, STATUS)[0]) == {"url": WS}
    hook = client.parse_webhook({"CallSid": "ex-sid-1", "Status": "busy"})
    assert hook.status == "busy"
    start = client.parse_stream_start(
        {"event": "start", "stream_sid": "st1", "start": {"call_sid": "ex-sid-1"}}
    )
    assert start == StreamStart("st1", "ex-sid-1")
    with pytest.raises(ValueError, match="App"):
        ExotelClient.validate_credentials({**creds, "app_id": ""})


async def test_vonage(api: list[httpx.Request]) -> None:
    client = VonageClient(
        VonageClient.validate_credentials({"application_id": "app-1", "private_key": PEM})
    )
    assert (await _place(client)).provider_call_id == "vg-uuid-1"
    sent = api[-1]
    token = sent.headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, KEY.public_key(), algorithms=["RS256"])
    assert claims["application_id"] == "app-1"
    body = json.loads(sent.content)
    assert body["to"] == [{"type": "phone", "number": "919845012345"}]
    assert body["answer_url"] == [ANSWER] and body["event_url"] == [STATUS]
    ncco = json.loads(client.answer_response(WS, STATUS)[0])
    assert ncco[0]["endpoint"][0] == {
        "type": "websocket",
        "uri": WS,
        "content-type": "audio/l16;rate=16000",
    }
    assert client.parse_webhook({"uuid": "vg-uuid-1", "status": "timeout"}).status == "no_answer"
    assert client.parse_stream_start({"event": "websocket:connected"}) is not None
    with pytest.raises(ValueError, match="private key"):
        VonageClient.validate_credentials({"application_id": "a", "private_key": "nope"})


async def test_provider_errors_are_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    real = httpx.AsyncClient
    reply = httpx.Response(400, json={"message": "The 'To' number is not a valid phone number."})
    monkeypatch.setattr(
        twilio_family.httpx,
        "AsyncClient",
        lambda *a, **k: real(*a, transport=httpx.MockTransport(lambda _: reply), **k),
    )
    with pytest.raises(TelephonyError, match="not a valid phone number"):
        await _place(TwilioClient({"account_sid": "AC1", "auth_token": "t"}))


async def test_new_providers_connect_from_integrations(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def verify(self: Any) -> None:
        raise TelephonyError("Twilio rejected the Account SID / Auth Token")

    monkeypatch.setattr(TwilioClient, "verify", verify)
    info = (await admin_client.get("/api/integrations")).json()["telephony_providers"]
    vonage_fields = next(p for p in info if p["id"] == "vonage")["fields"]
    assert [f["key"] for f in vonage_fields] == ["application_id", "private_key"]
    assert vonage_fields[1]["secret"] and vonage_fields[1]["multiline"]
    resp = await admin_client.post(
        "/api/integrations/telephony",
        json={
            "provider": "twilio",
            "name": "Twilio",
            "credentials": {"account_sid": "AC1", "auth_token": "t"},
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["last_error"] == "Twilio rejected the Account SID / Auth Token"
