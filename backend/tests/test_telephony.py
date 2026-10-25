import json
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

import httpx
import pytest
from pipecat.frames.frames import InputAudioRawFrame, InterruptionFrame, OutputAudioRawFrame

from ymva.telephony import TelephonyError
from ymva.telephony.base import StreamStart, reject_xml, stream_xml
from ymva.telephony.plivo_family import PlivoClient, VobizClient, VobizFrameSerializer
from ymva.voice.browser import BrowserAudioSerializer

CREDS = {"auth_id": "MAXXXXXXXXXXXXXXXXXX", "auth_token": "secret-token"}


class Recorder:
    def __init__(self, responses: list[httpx.Response]):
        self.requests: list[httpx.Request] = []
        self._responses = responses

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responses.pop(0)


def _patch_client(monkeypatch: pytest.MonkeyPatch, cls: type, recorder: Recorder) -> None:
    original = cls._client

    def patched(self: Any) -> httpx.AsyncClient:
        real = original(self)
        return httpx.AsyncClient(
            transport=httpx.MockTransport(recorder.handler),
            headers=real.headers,
            auth=real.auth,
        )

    monkeypatch.setattr(cls, "_client", patched)


def test_stream_xml_is_valid_and_escaped() -> None:
    xml = stream_xml(
        "wss://voice.example.com/api/telephony/plivo/ws/abc&d", "https://x/s?call=1&a=2"
    )
    root = ElementTree.fromstring(xml.split("\n", 1)[1])
    stream = root.find("Stream")
    assert stream is not None
    assert stream.text == "wss://voice.example.com/api/telephony/plivo/ws/abc&d"
    assert stream.get("bidirectional") == "true"
    assert stream.get("contentType") == "audio/x-mulaw;rate=8000"
    assert stream.get("statusCallbackUrl") == "https://x/s?call=1&a=2"
    assert (
        ElementTree.fromstring(reject_xml("Sorry <busy>").split("\n", 1)[1]).find("Hangup")
        is not None
    )


async def test_plivo_place_call(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder([httpx.Response(201, json={"request_uuid": "req-123", "message": "call fired"})])
    _patch_client(monkeypatch, PlivoClient, rec)
    placed = await PlivoClient(CREDS).place_call(
        from_number="+91 80 1234 5678",
        to_number="+919876543210",
        answer_url="https://v/answer?call=1",
        status_url="https://v/status?call=1",
        time_limit=630,
    )
    assert placed.provider_call_id == "req-123"
    request = rec.requests[0]
    assert str(request.url) == f"https://api.plivo.com/v1/Account/{CREDS['auth_id']}/Call/"
    assert request.headers["authorization"].startswith("Basic ")
    body = json.loads(request.content)
    assert body["from"] == "918012345678" and body["to"] == "919876543210"
    assert body["answer_method"] == "POST" and body["time_limit"] == 630


async def test_vobiz_uses_header_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder([httpx.Response(200, json={"request_uuid": "vz-1"}), httpx.Response(204)])
    _patch_client(monkeypatch, VobizClient, rec)
    client = VobizClient(CREDS)
    placed = await client.place_call(
        from_number="+918012345678",
        to_number="+919876543210",
        answer_url="https://v/a",
        status_url="https://v/s",
        time_limit=300,
    )
    await client.hang_up("call-9")
    assert placed.provider_call_id == "vz-1"
    create, hangup = rec.requests
    assert str(create.url) == f"https://api.vobiz.ai/api/v1/Account/{CREDS['auth_id']}/Call/"
    assert create.headers["x-auth-id"] == CREDS["auth_id"]
    assert create.headers["x-auth-token"] == CREDS["auth_token"]
    assert "authorization" not in create.headers
    assert hangup.method == "DELETE" and hangup.url.path.endswith("/Call/call-9/")


async def test_provider_errors_are_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(
        [httpx.Response(401), httpx.Response(400, json={"error": "invalid 'to' number"})]
    )
    _patch_client(monkeypatch, PlivoClient, rec)
    with pytest.raises(TelephonyError, match="rejected the Auth ID"):
        await PlivoClient(CREDS).verify()
    with pytest.raises(TelephonyError, match="invalid 'to' number"):
        await PlivoClient(CREDS).place_call(
            from_number="1", to_number="2", answer_url="a", status_url="s", time_limit=60
        )


def test_webhook_parsing_normalises_status() -> None:
    client = PlivoClient(CREDS)
    hook = client.parse_webhook(
        {"CallUUID": "c-1", "From": "919876543210", "To": "918012345678", "CallStatus": "no-answer"}
    )
    assert (hook.provider_call_id, hook.status) == ("c-1", "no_answer")
    busy = client.parse_webhook(
        {"CallUUID": "c-2", "CallStatus": "completed", "HangupCause": "USER_BUSY"}
    )
    assert busy.status == "busy"
    done = client.parse_webhook({"CallUUID": "c-3", "CallStatus": "completed", "Duration": "75"})
    assert (done.status, done.duration_seconds) == ("completed", 75)


def test_stream_start_both_shapes() -> None:
    client = VobizClient(CREDS)
    assert client.parse_stream_start({"event": "media"}) is None
    nested = client.parse_stream_start(
        {"event": "start", "start": {"streamId": "s1", "callId": "c1"}}
    )
    assert nested == StreamStart(stream_id="s1", provider_call_id="c1")
    flat = client.parse_stream_start({"event": "start", "streamId": "s2"})
    assert flat == StreamStart(stream_id="s2", provider_call_id=None)


def test_vobiz_serializer_from_client() -> None:
    serializer = VobizClient(CREDS).serializer(StreamStart(stream_id="s1", provider_call_id="c1"))
    assert isinstance(serializer, VobizFrameSerializer)
    # Without a call ID there is nothing to hang up, so auto hang-up is off instead of failing.
    VobizClient(CREDS).serializer(StreamStart(stream_id="s1", provider_call_id=None))


async def test_browser_serializer_roundtrip() -> None:
    serializer = BrowserAudioSerializer()
    await serializer.setup(SimpleNamespace(audio_in_sample_rate=16000))  # type: ignore[arg-type]
    pcm = b"\x01\x00" * 320  # 20 ms of 16 kHz audio
    frame = await serializer.deserialize(pcm)
    assert isinstance(frame, InputAudioRawFrame)
    assert frame.sample_rate == 16000 and frame.audio == pcm
    assert await serializer.deserialize("text is ignored") is None
    out = await serializer.serialize(
        OutputAudioRawFrame(audio=pcm, sample_rate=16000, num_channels=1)
    )
    assert out == pcm
    assert json.loads(str(await serializer.serialize(InterruptionFrame()))) == {"type": "clear"}
