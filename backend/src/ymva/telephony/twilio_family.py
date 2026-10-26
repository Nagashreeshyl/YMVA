"""Twilio, and Telnyx through its Twilio-compatible TeXML. Both answer a call with
<Connect><Stream>, report status with Twilio-style form fields, and stream mu-law audio
over a WebSocket (each with its own message format)."""

from typing import Any
from xml.sax.saxutils import escape, quoteattr

import httpx
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.serializers.telnyx import TelnyxFrameSerializer
from pipecat.serializers.twilio import TwilioFrameSerializer

from ymva.telephony.base import (
    CredentialField,
    PlacedCall,
    StreamStart,
    TelephonyError,
    TelephonyProviderClient,
    WebhookCall,
)

TIMEOUT = httpx.Timeout(15.0, connect=5.0)

_STATUS_MAP = {
    "queued": "ringing",
    "initiated": "ringing",
    "ringing": "ringing",
    "in-progress": "in_progress",
    "answered": "in_progress",
    "completed": "completed",
    "busy": "busy",
    "no-answer": "no_answer",
    "failed": "failed",
    "canceled": "canceled",
}


def _say_and_hang_up(message: str) -> tuple[str, str]:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<Response><Say>{escape(message)}</Say><Hangup/></Response>",
        "application/xml",
    )


def _parse_status(form: dict[str, str]) -> WebhookCall:
    duration = form.get("CallDuration") or form.get("Duration")
    return WebhookCall(
        provider_call_id=form.get("CallSid") or form.get("CallSidLegacy"),
        from_number=form.get("From"),
        to_number=form.get("To"),
        status=_STATUS_MAP.get((form.get("CallStatus") or "").lower()),
        duration_seconds=int(duration) if duration and duration.isdigit() else None,
        hangup_cause=form.get("SipResponseCode") or form.get("ErrorCode"),
    )


class TwilioClient(TelephonyProviderClient):
    name = "Twilio"
    fields = (
        CredentialField("account_sid", "Account SID", hint="Starts with AC"),
        CredentialField("auth_token", "Auth Token", secret=True),
    )
    setup = (
        "On the number's page in Twilio, set 'A call comes in' to Webhook = the answer URL "
        "below (POST), and the call status changes callback to the status URL."
    )

    @property
    def _base(self) -> str:
        return f"https://api.twilio.com/2010-04-01/Accounts/{self.credentials['account_sid']}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        auth = (str(self.credentials["account_sid"]), str(self.credentials["auth_token"]))
        try:
            async with httpx.AsyncClient(auth=auth, timeout=TIMEOUT) as client:
                response = await client.request(method, f"{self._base}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise TelephonyError(f"Couldn't reach Twilio: {exc.__class__.__name__}") from exc
        if response.status_code in (401, 403):
            raise TelephonyError("Twilio rejected the Account SID / Auth Token")
        if response.status_code >= 300:
            try:
                message = response.json().get("message", response.text)
            except ValueError:
                message = response.text
            raise TelephonyError(f"Twilio: {str(message)[:300]}")
        return response

    async def verify(self) -> None:
        await self._request("GET", ".json")

    async def place_call(
        self, *, from_number: str, to_number: str, answer_url: str, status_url: str, time_limit: int
    ) -> PlacedCall:
        response = await self._request(
            "POST",
            "/Calls.json",
            data={
                "From": from_number,
                "To": to_number,
                "Url": answer_url,
                "Method": "POST",
                "StatusCallback": status_url,
                "StatusCallbackMethod": "POST",
                "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
                "Timeout": 40,
                "TimeLimit": time_limit,
            },
        )
        return PlacedCall(provider_call_id=response.json().get("sid"))

    async def hang_up(self, provider_call_id: str) -> None:
        await self._request("POST", f"/Calls/{provider_call_id}.json", data={"Status": "completed"})

    def answer_response(self, ws_url: str, status_url: str) -> tuple[str, str]:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<Response><Connect><Stream url={quoteattr(ws_url)} "
            f'statusCallback={quoteattr(status_url)} statusCallbackMethod="POST"/>'
            "</Connect></Response>",
            "application/xml",
        )

    def reject_response(self, message: str) -> tuple[str, str]:
        return _say_and_hang_up(message)

    def parse_webhook(self, form: dict[str, str]) -> WebhookCall:
        return _parse_status(form)

    def parse_stream_start(self, message: dict[str, Any]) -> StreamStart | None:
        if message.get("event") != "start":
            return None  # "connected" comes first
        start = message.get("start") or {}
        stream_sid = start.get("streamSid") or message.get("streamSid")
        return StreamStart(stream_sid, start.get("callSid")) if stream_sid else None

    def serializer(self, start: StreamStart) -> FrameSerializer:
        return TwilioFrameSerializer(
            stream_sid=start.stream_id,
            call_sid=start.provider_call_id,
            account_sid=str(self.credentials["account_sid"]),
            auth_token=str(self.credentials["auth_token"]),
            params=TwilioFrameSerializer.InputParams(auto_hang_up=bool(start.provider_call_id)),
        )


class TelnyxClient(TelephonyProviderClient):
    name = "Telnyx"
    fields = (
        CredentialField("api_key", "API key", secret=True, hint="Starts with KEY"),
        CredentialField(
            "texml_app_id",
            "TeXML application ID",
            hint="Voice → TeXML Applications; put the answer URL below in its Voice URL",
        ),
    )
    setup = (
        "Create a TeXML application with Voice URL = the answer URL below (POST) and Status "
        "callback = the status URL, then assign your numbers to it."
    )

    @classmethod
    def credential_hint(cls, credentials: dict[str, Any]) -> str:
        app = str(credentials.get("texml_app_id", ""))
        return f"app …{app[-6:]}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.credentials['api_key']}"}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=TIMEOUT) as client:
                response = await client.request(
                    method, f"https://api.telnyx.com/v2{path}", **kwargs
                )
        except httpx.HTTPError as exc:
            raise TelephonyError(f"Couldn't reach Telnyx: {exc.__class__.__name__}") from exc
        if response.status_code in (401, 403):
            raise TelephonyError("Telnyx rejected the API key")
        if response.status_code >= 300:
            try:
                errors = response.json().get("errors") or [{}]
                message = errors[0].get("detail") or errors[0].get("title") or response.text
            except ValueError:
                message = response.text
            raise TelephonyError(f"Telnyx: {str(message)[:300]}")
        return response

    async def verify(self) -> None:
        await self._request("GET", "/balance")

    async def place_call(
        self, *, from_number: str, to_number: str, answer_url: str, status_url: str, time_limit: int
    ) -> PlacedCall:
        response = await self._request(
            "POST",
            f"/texml/calls/{self.credentials['texml_app_id']}",
            json={
                "From": from_number,
                "To": to_number,
                "Url": answer_url,
                "UrlMethod": "POST",
                "StatusCallback": status_url,
                "StatusCallbackMethod": "POST",
                "Timeout": 40,
                "TimeLimit": time_limit,
            },
        )
        data = response.json().get("data") or {}
        return PlacedCall(provider_call_id=data.get("call_sid") or data.get("sid"))

    async def hang_up(self, provider_call_id: str) -> None:
        await self._request(
            "POST", f"/texml/calls/{provider_call_id}/update", json={"Status": "completed"}
        )

    def answer_response(self, ws_url: str, status_url: str) -> tuple[str, str]:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<Response><Connect><Stream url={quoteattr(ws_url)} "
            'bidirectionalMode="rtp" bidirectionalCodec="PCMU"/></Connect></Response>',
            "application/xml",
        )

    def reject_response(self, message: str) -> tuple[str, str]:
        return _say_and_hang_up(message)

    def parse_webhook(self, form: dict[str, str]) -> WebhookCall:
        return _parse_status(form)

    def parse_stream_start(self, message: dict[str, Any]) -> StreamStart | None:
        if message.get("event") != "start":
            return None
        start = message.get("start") or {}
        stream_id = message.get("stream_id") or start.get("stream_id")
        return StreamStart(stream_id, start.get("call_control_id")) if stream_id else None

    def serializer(self, start: StreamStart) -> FrameSerializer:
        return TelnyxFrameSerializer(
            stream_id=start.stream_id,
            outbound_encoding="PCMU",
            inbound_encoding="PCMU",
            call_control_id=start.provider_call_id,
            api_key=str(self.credentials["api_key"]),
            params=TelnyxFrameSerializer.InputParams(auto_hang_up=bool(start.provider_call_id)),
        )
