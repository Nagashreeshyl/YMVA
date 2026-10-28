"""Simulator: a phone provider that dials into `ymva simulate` instead of real phones.

Calls placed through it are queued in Redis for the simulated phone network, which answers
them like the chosen real provider (Twilio, Telnyx, Exotel or Vonage format). Everything else
(webhooks, audio, campaigns, analysis) runs exactly as for real calls. Hidden from the
Integrations page; `ymva simulate` creates its own accounts.
"""

import json
import uuid
from typing import Any

from pipecat.serializers.base_serializer import FrameSerializer

from ymva.redis import get_redis
from ymva.telephony.base import (
    CredentialField,
    PlacedCall,
    StreamStart,
    TelephonyProviderClient,
    WebhookCall,
)
from ymva.telephony.exotel import ExotelClient
from ymva.telephony.plivo_family import PlivoClient
from ymva.telephony.twilio_family import TelnyxClient, TwilioClient
from ymva.telephony.vonage import VonageClient

QUEUE_KEY = "sim:placed"
TRANSFERS_KEY = "sim:transfers"
FORMATS: dict[str, type[TelephonyProviderClient]] = {
    "twilio": TwilioClient,
    "telnyx": TelnyxClient,
    "exotel": ExotelClient,
    "vonage": VonageClient,
    "plivo": PlivoClient,  # Plivo's media protocol, as Vobiz uses
}
# Credentials the real clients need to build their serializers; never used to reach anyone.
_PLACEHOLDER = {
    "account_sid": "ACsimulator",
    "auth_token": "simulator",
    "api_key": "simulator",
    "texml_app_id": "simulator",
    "auth_id": "MAsimulator",
}


class SimulatorClient(TelephonyProviderClient):
    name = "Simulator"
    fields = (
        CredentialField("format", "Speaks like", hint="twilio, telnyx, exotel, vonage or plivo"),
    )

    @classmethod
    def validate_credentials(cls, credentials: dict[str, Any]) -> dict[str, str]:
        cleaned = super().validate_credentials(credentials)
        if cleaned["format"] not in FORMATS:
            raise ValueError("Format must be twilio, telnyx, exotel, vonage or plivo")
        return cleaned

    @property
    def _like(self) -> TelephonyProviderClient:
        return FORMATS[str(self.credentials["format"])](_PLACEHOLDER)

    async def verify(self) -> None:
        return None

    async def place_call(
        self, *, from_number: str, to_number: str, answer_url: str, status_url: str, time_limit: int
    ) -> PlacedCall:
        sid = f"SIM-{uuid.uuid4().hex[:16]}"
        await get_redis().rpush(
            QUEUE_KEY,
            json.dumps(
                {
                    "format": self.credentials["format"],
                    "sid": sid,
                    "from": from_number,
                    "to": to_number,
                    "answer_url": answer_url,
                    "status_url": status_url,
                }
            ),
        )
        return PlacedCall(provider_call_id=sid)

    async def hang_up(self, provider_call_id: str) -> None:
        return None

    async def transfer(
        self, provider_call_id: str, *, url: str, to_number: str, caller_id: str
    ) -> None:
        payload = {"sid": provider_call_id, "url": url, "to": to_number, "from": caller_id}
        await get_redis().rpush(TRANSFERS_KEY, json.dumps(payload))

    def transfer_response(self, to_number: str, caller_id: str) -> tuple[str, str]:
        return self._like.transfer_response(to_number, caller_id)

    def answer_response(self, ws_url: str, status_url: str) -> tuple[str, str]:
        return self._like.answer_response(ws_url, status_url)

    def reject_response(self, message: str) -> tuple[str, str]:
        return self._like.reject_response(message)

    def parse_webhook(self, form: dict[str, str]) -> WebhookCall:
        return self._like.parse_webhook(form)

    def parse_stream_start(self, message: dict[str, Any]) -> StreamStart | None:
        return self._like.parse_stream_start(message)

    def serializer(self, start: StreamStart) -> FrameSerializer:
        serializer = self._like.serializer(start)
        # Nothing to hang up at the end: the simulated network ends the call itself.
        params = getattr(serializer, "_params", None)
        if params is not None and hasattr(params, "auto_hang_up"):
            params.auto_hang_up = False
        return serializer
