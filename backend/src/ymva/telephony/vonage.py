"""Vonage Voice API. Calls are answered with an NCCO that connects the audio to a WebSocket
(16 kHz linear PCM); requests are signed with the Vonage application's private key."""

import json
import time
import uuid
from typing import Any

import httpx
import jwt
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.serializers.vonage import VonageFrameSerializer

from ymva.telephony.base import (
    CredentialField,
    PlacedCall,
    StreamStart,
    TelephonyError,
    TelephonyProviderClient,
    WebhookCall,
    digits,
)

TIMEOUT = httpx.Timeout(15.0, connect=5.0)
API = "https://api.nexmo.com/v1/calls"

_STATUS_MAP = {
    "started": "ringing",
    "ringing": "ringing",
    "answered": "in_progress",
    "completed": "completed",
    "busy": "busy",
    "timeout": "no_answer",
    "unanswered": "no_answer",
    "rejected": "busy",
    "failed": "failed",
    "cancelled": "canceled",
}


class VonageClient(TelephonyProviderClient):
    name = "Vonage"
    fields = (
        CredentialField("application_id", "Application ID"),
        CredentialField(
            "private_key",
            "Private key",
            secret=True,
            multiline=True,
            hint="The private.key file downloaded when the Vonage application was created",
        ),
    )
    setup = (
        "In the Vonage application's Voice settings, set the Answer URL to the answer URL below "
        "(GET) and the Event URL to the status URL (POST), then link your numbers to it."
    )

    @classmethod
    def validate_credentials(cls, credentials: dict[str, Any]) -> dict[str, str]:
        cleaned = super().validate_credentials(credentials)
        if "PRIVATE KEY" not in cleaned["private_key"]:
            raise ValueError("Paste the whole private key, including the BEGIN/END lines")
        return cleaned

    def _token(self) -> str:
        now = int(time.time())
        claims = {
            "application_id": self.credentials["application_id"],
            "iat": now,
            "exp": now + 300,
            "jti": str(uuid.uuid4()),
        }
        try:
            return jwt.encode(claims, str(self.credentials["private_key"]), algorithm="RS256")
        except (ValueError, TypeError) as exc:
            raise TelephonyError("The Vonage private key isn't valid") from exc

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token()}"}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=TIMEOUT) as client:
                response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise TelephonyError(f"Couldn't reach Vonage: {exc.__class__.__name__}") from exc
        if response.status_code in (401, 403):
            raise TelephonyError("Vonage rejected the application ID / private key")
        if response.status_code >= 300:
            raise TelephonyError(f"Vonage: {response.text[:300]}")
        return response

    async def verify(self) -> None:
        await self._request("GET", API, params={"page_size": 1})

    async def place_call(
        self, *, from_number: str, to_number: str, answer_url: str, status_url: str, time_limit: int
    ) -> PlacedCall:
        response = await self._request(
            "POST",
            API,
            json={
                "to": [{"type": "phone", "number": digits(to_number)}],
                "from": {"type": "phone", "number": digits(from_number)},
                "answer_url": [answer_url],
                "answer_method": "GET",
                "event_url": [status_url],
                "event_method": "POST",
                "ringing_timer": 40,
                "length_timer": time_limit,
            },
        )
        return PlacedCall(provider_call_id=response.json().get("uuid"))

    async def hang_up(self, provider_call_id: str) -> None:
        await self._request("PUT", f"{API}/{provider_call_id}", json={"action": "hangup"})

    def answer_response(self, ws_url: str, status_url: str) -> tuple[str, str]:
        ncco = [
            {
                "action": "connect",
                "eventUrl": [status_url],
                "endpoint": [
                    {"type": "websocket", "uri": ws_url, "content-type": "audio/l16;rate=16000"}
                ],
            }
        ]
        return json.dumps(ncco), "application/json"

    def reject_response(self, message: str) -> tuple[str, str]:
        return json.dumps([{"action": "talk", "text": message}]), "application/json"

    def parse_webhook(self, form: dict[str, str]) -> WebhookCall:
        duration = form.get("duration")
        return WebhookCall(
            provider_call_id=form.get("uuid") or form.get("conversation_uuid"),
            from_number=form.get("from"),
            to_number=form.get("to"),
            status=_STATUS_MAP.get((form.get("status") or "").lower()),
            duration_seconds=int(duration) if duration and duration.isdigit() else None,
            hangup_cause=form.get("detail") or form.get("reason"),
        )

    def parse_stream_start(self, message: dict[str, Any]) -> StreamStart | None:
        # Vonage's first text message; the call is identified by the single-use URL itself.
        if message.get("event") == "websocket:connected":
            return StreamStart(stream_id="vonage", provider_call_id=None)
        return None

    def serializer(self, start: StreamStart) -> FrameSerializer:
        return VonageFrameSerializer()
