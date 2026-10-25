"""Telephony provider interface. Each provider places calls, answers webhooks with stream XML,
and supplies the Pipecat serializer for its media WebSocket."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from pipecat.serializers.base_serializer import FrameSerializer


class TelephonyError(Exception):
    """A provider rejected a request; the message is safe to show to admins."""


@dataclass
class PlacedCall:
    provider_call_id: str | None


@dataclass
class StreamStart:
    stream_id: str
    provider_call_id: str | None


@dataclass
class WebhookCall:
    """Normalised fields from an answer / status webhook."""

    provider_call_id: str | None
    from_number: str | None
    to_number: str | None
    status: str | None
    duration_seconds: int | None
    hangup_cause: str | None


def digits(number: str) -> str:
    """Providers in this family expect numbers as digits only, country code first."""
    return "".join(ch for ch in number if ch.isdigit())


def stream_xml(ws_url: str, status_url: str) -> str:
    """Answer XML that connects the call audio to our WebSocket in both directions."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>"
        f'<Stream bidirectional="true" keepCallAlive="true" audioTrack="inbound" '
        f'contentType="audio/x-mulaw;rate=8000" streamTimeout="3600" '
        f'statusCallbackUrl={quoteattr(status_url)} statusCallbackMethod="POST">'
        f"{escape(ws_url)}</Stream>"
        "</Response>"
    )


def reject_xml(message: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Response><Speak language="en-IN">{escape(message)}</Speak><Hangup/></Response>'
    )


class TelephonyProviderClient(ABC):
    name: str

    def __init__(self, credentials: dict[str, Any]):
        self.credentials = credentials

    @staticmethod
    @abstractmethod
    def validate_credentials(credentials: dict[str, Any]) -> dict[str, str]:
        """Return the cleaned credential dict, or raise ValueError."""

    @staticmethod
    def credential_hint(credentials: dict[str, Any]) -> str:
        auth_id = str(credentials.get("auth_id", ""))
        return f"{auth_id[:4]}…{auth_id[-4:]}" if len(auth_id) > 8 else auth_id

    @abstractmethod
    async def verify(self) -> None:
        """Make a free API call that proves the credentials work; raise TelephonyError."""

    @abstractmethod
    async def place_call(
        self, *, from_number: str, to_number: str, answer_url: str, status_url: str, time_limit: int
    ) -> PlacedCall: ...

    @abstractmethod
    async def hang_up(self, provider_call_id: str) -> None: ...

    @abstractmethod
    def parse_webhook(self, form: dict[str, str]) -> WebhookCall: ...

    @abstractmethod
    def parse_stream_start(self, message: dict[str, Any]) -> StreamStart | None:
        """Return stream details from the first WebSocket messages, or None to keep waiting."""

    @abstractmethod
    def serializer(self, start: StreamStart) -> FrameSerializer: ...
