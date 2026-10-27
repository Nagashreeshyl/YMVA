"""Telephony provider interface. Each provider places calls, answers webhooks with stream XML,
and supplies the Pipecat serializer for its media WebSocket."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar
from xml.sax.saxutils import escape, quoteattr

from pipecat.serializers.base_serializer import FrameSerializer


class TelephonyError(Exception):
    """A provider rejected a request; the message is safe to show to admins."""


@dataclass(frozen=True)
class CredentialField:
    """One box on the Integrations form for connecting an account."""

    key: str
    label: str
    secret: bool = False
    hint: str = ""
    multiline: bool = False


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


def dial_xml(to_number: str, caller_id: str) -> str:
    """Plivo-style XML that connects the caller to another phone."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<Response><Dial callerId={quoteattr(digits(caller_id))}>"
        f"<Number>{digits(to_number)}</Number></Dial></Response>"
    )


def reject_xml(message: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Response><Speak language="en-IN">{escape(message)}</Speak><Hangup/></Response>'
    )


class TelephonyProviderClient(ABC):
    name: str
    fields: ClassVar[tuple[CredentialField, ...]]
    # How to send this provider's calls to YMVA, shown next to the answer/status URLs.
    setup: ClassVar[str] = ""

    def __init__(self, credentials: dict[str, Any]):
        self.credentials = credentials

    @classmethod
    def validate_credentials(cls, credentials: dict[str, Any]) -> dict[str, str]:
        """Return the cleaned credential dict, or raise ValueError."""
        cleaned = {f.key: str(credentials.get(f.key) or "").strip() for f in cls.fields}
        missing = [f.label for f in cls.fields if not cleaned[f.key]]
        if missing:
            raise ValueError(f"Fill in: {', '.join(missing)}")
        return cleaned

    @classmethod
    def credential_hint(cls, credentials: dict[str, Any]) -> str:
        first = str(credentials.get(cls.fields[0].key, ""))
        return f"{first[:4]}…{first[-4:]}" if len(first) > 8 else first

    def answer_response(self, ws_url: str, status_url: str) -> tuple[str, str]:
        """Body and media type telling the provider to stream the call audio to ws_url."""
        return stream_xml(ws_url, status_url), "application/xml"

    def reject_response(self, message: str) -> tuple[str, str]:
        return reject_xml(message), "application/xml"

    def transfer_response(self, to_number: str, caller_id: str) -> tuple[str, str]:
        """Body served at the transfer URL: connect the caller to to_number."""
        return dial_xml(to_number, caller_id), "application/xml"

    async def transfer(
        self, provider_call_id: str, *, url: str, to_number: str, caller_id: str
    ) -> None:
        """Move the live call off the AI to a person: the provider fetches `url` (which serves
        transfer_response) or is given to_number directly. Raise TelephonyError on failure."""
        raise TelephonyError(f"{self.name} can't transfer a call from the AI agent")

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
