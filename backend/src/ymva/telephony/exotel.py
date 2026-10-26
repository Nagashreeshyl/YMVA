"""Exotel. Calls run through an Exotel app (flow) whose Voicebot applet asks YMVA, at the answer
URL, which WebSocket to stream to; YMVA replies with a single-use WebSocket address."""

import json
from typing import Any

import httpx
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.serializers.exotel import ExotelFrameSerializer

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
    "ringing": "ringing",
    "in-progress": "in_progress",
    "completed": "completed",
    "busy": "busy",
    "no-answer": "no_answer",
    "failed": "failed",
    "canceled": "canceled",
}


class ExotelClient(TelephonyProviderClient):
    name = "Exotel"
    fields = (
        CredentialField("api_key", "API key"),
        CredentialField("api_token", "API token", secret=True),
        CredentialField("account_sid", "Account SID"),
        CredentialField(
            "subdomain",
            "API host",
            hint="api.in.exotel.com for the Mumbai cluster, api.exotel.com for Singapore",
        ),
        CredentialField(
            "app_id",
            "App (flow) ID",
            hint="The flow with the Voicebot applet; its number appears in the flow's URL",
        ),
    )
    setup = (
        "In App Bazaar, make a flow with a Voicebot applet whose URL is the answer URL below "
        "(it returns the WebSocket address for each call), followed by a Hangup applet. Use "
        "that flow for your ExoPhone's incoming calls, and put its ID above for outgoing calls. "
        "Set the status URL as the flow's status callback."
    )

    @property
    def _base(self) -> str:
        host = str(self.credentials["subdomain"]).removeprefix("https://").strip("/")
        return f"https://{host}/v1/Accounts/{self.credentials['account_sid']}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        auth = (str(self.credentials["api_key"]), str(self.credentials["api_token"]))
        try:
            async with httpx.AsyncClient(auth=auth, timeout=TIMEOUT) as client:
                response = await client.request(method, f"{self._base}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise TelephonyError(f"Couldn't reach Exotel: {exc.__class__.__name__}") from exc
        if response.status_code in (401, 403):
            raise TelephonyError("Exotel rejected the API key / token")
        if response.status_code >= 300:
            raise TelephonyError(f"Exotel: {response.text[:300]}")
        return response

    async def verify(self) -> None:
        await self._request("GET", ".json")

    async def place_call(
        self, *, from_number: str, to_number: str, answer_url: str, status_url: str, time_limit: int
    ) -> PlacedCall:
        flow = (
            f"http://my.exotel.com/{self.credentials['account_sid']}/exoml/start_voice/"
            f"{self.credentials['app_id']}"
        )
        response = await self._request(
            "POST",
            "/Calls/connect.json",
            data={
                "From": to_number,  # Exotel calls "From" first: that's the customer
                "CallerId": from_number,  # the ExoPhone
                "Url": flow,
                "CallType": "trans",
                "TimeLimit": time_limit,
                "TimeOut": 40,
                "StatusCallback": status_url,
                "StatusCallbackEvents[0]": "terminal",
                "StatusCallbackContentType": "application/json",
            },
        )
        call = response.json().get("Call") or {}
        return PlacedCall(provider_call_id=call.get("Sid"))

    async def hang_up(self, provider_call_id: str) -> None:
        # Exotel has no API to end a live call; closing the stream ends the Voicebot applet
        # and the flow moves on to its Hangup applet.
        return None

    def answer_response(self, ws_url: str, status_url: str) -> tuple[str, str]:
        return json.dumps({"url": ws_url}), "application/json"

    def reject_response(self, message: str) -> tuple[str, str]:
        return json.dumps({"url": "", "message": message}), "application/json"

    def parse_webhook(self, form: dict[str, str]) -> WebhookCall:
        duration = form.get("ConversationDuration") or form.get("Duration")
        return WebhookCall(
            provider_call_id=form.get("CallSid"),
            from_number=form.get("From") or form.get("CallFrom"),
            to_number=form.get("To") or form.get("CallTo"),
            status=_STATUS_MAP.get((form.get("Status") or form.get("CallStatus") or "").lower()),
            duration_seconds=int(duration) if duration and duration.isdigit() else None,
            hangup_cause=form.get("Status"),
        )

    def parse_stream_start(self, message: dict[str, Any]) -> StreamStart | None:
        if message.get("event") != "start":
            return None
        start = message.get("start") or {}
        stream_sid = message.get("stream_sid") or start.get("stream_sid")
        return StreamStart(stream_sid, start.get("call_sid")) if stream_sid else None

    def serializer(self, start: StreamStart) -> FrameSerializer:
        return ExotelFrameSerializer(stream_sid=start.stream_id, call_sid=start.provider_call_id)
