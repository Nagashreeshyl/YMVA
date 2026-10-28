"""Plivo and Vobiz. Vobiz implements Plivo's call API, answer XML and media-stream protocol;
they differ in API host and authentication."""

import json
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

import httpx
from loguru import logger
from pipecat.frames.frames import Frame
from pipecat.serializers.plivo import PlivoFrameSerializer

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

# Normalise both providers' call statuses / hangup causes to YMVA call statuses.
_STATUS_MAP = {
    "completed": "completed",
    "busy": "busy",
    "no-answer": "no_answer",
    "noanswer": "no_answer",
    "cancel": "canceled",
    "canceled": "canceled",
    "cancelled": "canceled",
    "failed": "failed",
    "timeout": "no_answer",
    "ringing": "ringing",
    "in-progress": "in_progress",
}


def _first(form: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        if form.get(key):
            return form[key]
    return None


class _PlivoFamily(TelephonyProviderClient):
    api_base: ClassVar[str]
    fields = (
        CredentialField("auth_id", "Auth ID"),
        CredentialField("auth_token", "Auth Token", secret=True),
    )
    setup = (
        "In the application for your number, set the Answer URL to the answer URL below "
        "(POST) and the Hangup URL to the status URL."
    )

    @property
    def auth_id(self) -> str:
        return str(self.credentials["auth_id"])

    @property
    def account_path(self) -> str:
        return f"/Account/{self.auth_id}"

    def _client(self) -> httpx.AsyncClient:
        raise NotImplementedError

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            async with self._client() as client:
                response = await client.request(method, f"{self.api_base}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise TelephonyError(f"Couldn't reach {self.name}: {exc.__class__.__name__}") from exc
        if response.status_code in (401, 403):
            raise TelephonyError(f"{self.name} rejected the Auth ID / Auth Token")
        return response

    @staticmethod
    def _error_text(response: httpx.Response) -> str:
        try:
            body = response.json()
            return str(body.get("error") or body.get("message") or body)[:300]
        except ValueError:
            return response.text[:300]

    async def place_call(
        self, *, from_number: str, to_number: str, answer_url: str, status_url: str, time_limit: int
    ) -> PlacedCall:
        payload = {
            "from": digits(from_number),
            "to": digits(to_number),
            "answer_url": answer_url,
            "answer_method": "POST",
            "hangup_url": status_url,
            "hangup_method": "POST",
            "ring_timeout": 40,
            "time_limit": time_limit,
        }
        response = await self._request("POST", f"{self.account_path}/Call/", json=payload)
        if response.status_code >= 300:
            raise TelephonyError(
                f"{self.name} couldn't place the call: {self._error_text(response)}"
            )
        body = response.json() if response.content else {}
        ids = body.get("request_uuid") or body.get("call_uuid") or body.get("requestUuid")
        if isinstance(ids, list):
            ids = ids[0] if ids else None
        return PlacedCall(provider_call_id=str(ids) if ids else None)

    async def hang_up(self, provider_call_id: str) -> None:
        response = await self._request("DELETE", f"{self.account_path}/Call/{provider_call_id}/")
        if response.status_code not in (200, 204, 404):
            raise TelephonyError(f"{self.name} couldn't hang up: {self._error_text(response)}")

    async def transfer(
        self, provider_call_id: str, *, url: str, to_number: str, caller_id: str
    ) -> None:
        response = await self._request(
            "POST",
            f"{self.account_path}/Call/{provider_call_id}/",
            json={"legs": "aleg", "aleg_url": url, "aleg_method": "POST"},
        )
        if response.status_code >= 300:
            raise TelephonyError(f"{self.name} couldn't transfer: {self._error_text(response)}")

    def parse_webhook(self, form: dict[str, str]) -> WebhookCall:
        raw_status = (_first(form, "CallStatus", "call_status", "Status") or "").lower()
        cause = _first(form, "HangupCause", "HangupCauseName", "hangup_cause")
        status = _STATUS_MAP.get(raw_status)
        if status == "completed" and cause and "busy" in cause.lower():
            status = "busy"
        duration = _first(form, "Duration", "BillDuration", "duration")
        return WebhookCall(
            provider_call_id=_first(form, "CallUUID", "call_uuid", "CallUuid"),
            from_number=_first(form, "From", "from"),
            to_number=_first(form, "To", "to"),
            status=status,
            duration_seconds=int(duration) if duration and duration.isdigit() else None,
            hangup_cause=cause,
        )

    def parse_stream_start(self, message: dict[str, Any]) -> StreamStart | None:
        if message.get("event") != "start":
            return None
        start = message.get("start") or {}
        stream_id = start.get("streamId") or message.get("streamId")
        if not stream_id:
            return None
        call_id = start.get("callId") or message.get("callId") or start.get("call_uuid")
        return StreamStart(stream_id=str(stream_id), provider_call_id=call_id)


class PlivoClient(_PlivoFamily):
    name = "Plivo"
    api_base = "https://api.plivo.com/v1"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            auth=(self.auth_id, str(self.credentials["auth_token"])), timeout=TIMEOUT
        )

    async def verify(self) -> None:
        response = await self._request("GET", f"{self.account_path}/")
        if response.status_code != 200:
            raise TelephonyError(f"Plivo check failed: {self._error_text(response)}")

    def serializer(self, start: StreamStart) -> PlivoFrameSerializer:
        return CheckpointSerializer(
            stream_id=start.stream_id,
            call_id=start.provider_call_id,
            auth_id=self.auth_id,
            auth_token=str(self.credentials["auth_token"]),
            params=PlivoFrameSerializer.InputParams(auto_hang_up=bool(start.provider_call_id)),
        )


class CheckpointSerializer(PlivoFrameSerializer):
    """Plivo's media protocol, passing the phone's playback confirmations ("playedStream" for a
    checkpoint we sent) to `played`, so the call can tell how far behind the phone is."""

    played: Callable[[str], Awaitable[None]] | None = None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if self.played is not None and isinstance(data, str) and '"playedStream"' in data:
            try:
                message = json.loads(data)
            except ValueError:
                message = {}
            if message.get("event") == "playedStream":
                await self.played(str(message.get("name", "")))
                return None
        return await super().deserialize(data)


class VobizFrameSerializer(CheckpointSerializer):
    """Plivo's media protocol, with hang-up through Vobiz's API."""

    async def _hang_up_call(self) -> None:
        url = f"https://api.vobiz.ai/api/v1/Account/{self._auth_id}/Call/{self._call_id}"
        headers = {"X-Auth-ID": str(self._auth_id), "X-Auth-Token": str(self._auth_token)}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.delete(url, headers=headers)
            if response.status_code not in (200, 204, 404):
                logger.error(f"Vobiz hang-up failed: {response.status_code} {response.text[:200]}")
        except httpx.HTTPError as exc:
            logger.error(f"Vobiz hang-up failed: {exc}")


class VobizClient(_PlivoFamily):
    name = "Vobiz"
    api_base = "https://api.vobiz.ai/api/v1"

    def _client(self) -> httpx.AsyncClient:
        headers = {
            "X-Auth-ID": self.auth_id,
            "X-Auth-Token": str(self.credentials["auth_token"]),
        }
        return httpx.AsyncClient(headers=headers, timeout=TIMEOUT)

    async def verify(self) -> None:
        response = await self._request("GET", f"{self.account_path}/concurrency")
        if response.status_code != 200:
            raise TelephonyError(f"Vobiz check failed: {self._error_text(response)}")

    def serializer(self, start: StreamStart) -> VobizFrameSerializer:
        return VobizFrameSerializer(
            stream_id=start.stream_id,
            call_id=start.provider_call_id,
            auth_id=self.auth_id,
            auth_token=str(self.credentials["auth_token"]),
            params=PlivoFrameSerializer.InputParams(auto_hang_up=bool(start.provider_call_id)),
        )
