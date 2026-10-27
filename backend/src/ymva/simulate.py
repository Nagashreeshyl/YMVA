"""`ymva simulate`: spoken test calls through a simulated phone network.

Use it as a regression check after changes ("does every call still greet, answer and get an
outcome?") and as a load test (`--calls 20`: how do replies and CPU hold up?). It uses the real
agent, real AI services (so it costs a little) and the real call path: webhooks, media
streams in Twilio/Telnyx/Exotel/Vonage formats, the campaign dialer and after-call analysis.

Half the calls ring in to simulator numbers; the other half are a campaign the dialer calls
out. A simulated customer says a short Kannada line and an English promise to pay. Everything
it creates is removed afterwards unless --keep is given.
"""

import asyncio
import audioop  # deprecated in 3.13; Pipecat on 3.12 still uses it too
import base64
import io
import json
import os
import re
import time
import uuid
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx
import numpy as np
import websockets
from sqlalchemy import delete, func, select

from ymva.db import get_sessionmaker
from ymva.models import (
    Agent,
    AgentStatus,
    AgentVersion,
    Call,
    CallEvent,
    CallEventType,
    Campaign,
    CampaignContact,
    CampaignStatus,
    PhoneNumber,
    ProviderAccount,
    TelephonyProvider,
)
from ymva.redis import get_redis
from ymva.security import encrypt_json
from ymva.services.integrations import load_ai_keys, new_webhook_secret, webhook_urls
from ymva.telephony.simulator import FORMATS, QUEUE_KEY
from ymva.voice.config import AgentConfig, detected_variables

LOCAL = os.environ.get("SIMULATE_BASE_URL", "http://127.0.0.1:8000")
LINES = [
    ("ಹೌದು, ನಾನೇ ಮಾತಾಡ್ತಿರೋದು.", "kn-IN"),
    ("Okay, I will pay the interest on the twentieth.", "en-IN"),
]
SAMPLE_VALUES = {
    "customer_name": "Ramesh",
    "patient_name": "Ramesh",
    "loan_id": "YM001",
    "due_amount": "50000",
    "due_date": "24/11/2026",
}
# Average level (RMS) of a 20 ms chunk that counts as the agent speaking: speech is well
# above it, the office background (about 400 RMS at full volume) well below.
LOUD = 1200
CHUNK = 640  # 20 ms of 16 kHz audio


@dataclass
class Result:
    label: str
    greeting_s: float | None = None
    replies_s: list[float | None] = field(default_factory=list)
    call_id: uuid.UUID | None = None
    outcome: str | None = None
    errors: int = 0
    failure: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.failure is None
            and self.greeting_s is not None
            and any(r is not None for r in self.replies_s)
            and self.outcome is not None
            and self.errors == 0
        )


def _local(url: str) -> str:
    """Webhooks carry the public address; the simulator talks to the app directly."""
    return re.sub(
        r"^(https?|wss?)://[^/]+",
        lambda m: ("ws" if m.group(1).startswith("ws") else "http") + "://" + LOCAL.split("://")[1],
        url,
    )


async def _speech(api_key: str, text: str, language: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.sarvam.ai/text-to-speech",
            headers={"api-subscription-key": api_key},
            json={
                "text": text,
                "target_language_code": language,
                "model": "bulbul:v3",
                "speaker": "rahul",
                "speech_sample_rate": 16000,
            },
        )
    response.raise_for_status()
    with wave.open(io.BytesIO(base64.b64decode(response.json()["audios"][0]))) as w:
        return bytes(w.readframes(w.getnframes()))


def _stream_url(fmt: str, body: str) -> str:
    if fmt == "exotel":
        return str(json.loads(body)["url"])
    if fmt == "vonage":
        return str(json.loads(body)[0]["endpoint"][0]["uri"])
    answer = ElementTree.fromstring(body.split("\n", 1)[1])  # noqa: S314 (our own app's reply)
    stream = answer.find("Connect/Stream")
    url = stream.get("url") if stream is not None else None
    if not url:
        raise ValueError(f"no audio stream in the answer: {body[:300]}")
    return url


class Customer:
    """The far end of one call, speaking the provider's media protocol."""

    def __init__(self, fmt: str, result: Result) -> None:
        self.fmt, self.result = fmt, result
        self.loud: list[float] = []
        self.start = time.monotonic()

    def _now(self) -> float:
        return time.monotonic() - self.start

    def _encode(self, pcm16k: bytes) -> bytes:
        if self.fmt == "vonage":
            return pcm16k
        pcm8k, _ = audioop.ratecv(pcm16k, 2, 1, 16000, 8000, None)
        return pcm8k if self.fmt == "exotel" else audioop.lin2ulaw(pcm8k, 2)

    def _decode(self, data: bytes) -> bytes:
        if self.fmt in ("vonage", "exotel"):
            return data
        return audioop.ulaw2lin(data, 2)

    async def talk(self, ws_url: str, sid: str, clips: list[bytes]) -> None:
        async with websockets.connect(_local(ws_url), max_size=None) as ws:
            stream = f"stream-{sid}"
            starts: dict[str, dict[str, Any]] = {
                "twilio": {"event": "start", "start": {"streamSid": stream, "callSid": sid}},
                "telnyx": {
                    "event": "start",
                    "stream_id": stream,
                    "start": {"call_control_id": sid},
                },
                "exotel": {
                    "event": "start",
                    "stream_sid": stream,
                    "start": {"stream_sid": stream, "call_sid": sid},
                },
                "vonage": {"event": "websocket:connected", "content-type": "audio/l16;rate=16000"},
            }
            start = starts[self.fmt]
            if self.fmt == "twilio":
                await ws.send(json.dumps({"event": "connected"}))
            await ws.send(json.dumps(start))

            async def listen() -> None:
                async for message in ws:
                    if isinstance(message, bytes):
                        audio = self._decode(message)
                    else:
                        event = json.loads(message)
                        if event.get("event") != "media":
                            continue
                        audio = self._decode(base64.b64decode(event["media"]["payload"]))
                    samples = np.frombuffer(audio, np.int16)
                    level = np.sqrt(np.mean(samples.astype(np.float64) ** 2)) if samples.size else 0
                    if level > LOUD:
                        self.loud.append(self._now())

            async def send(pcm: bytes) -> None:
                for i in range(0, len(pcm), CHUNK):
                    data = self._encode(pcm[i : i + CHUNK])
                    if self.fmt == "vonage":
                        await ws.send(data)
                    else:
                        media: dict[str, Any] = {
                            "event": "media",
                            "media": {"payload": base64.b64encode(data).decode()},
                        }
                        if self.fmt == "exotel":
                            media["stream_sid"] = stream
                        await ws.send(json.dumps(media))
                    await asyncio.sleep(0.02)

            async def wait_for_quiet(seconds: float, give_up: float) -> None:
                """Keep the line open until the agent has been quiet a while (it may pause
                between sentences), like a person waiting for their turn."""
                deadline = self._now() + give_up
                while self._now() < deadline:
                    if self.loud and self._now() - self.loud[-1] >= seconds:
                        return
                    await send(silence * 5)

            listener = asyncio.create_task(listen())
            silence = b"\0\0" * (CHUNK // 2)
            await send(silence * 400)  # 8 s: the agent must greet without being spoken to
            self.result.greeting_s = self.loud[0] if self.loud else None
            for clip in clips:
                await wait_for_quiet(1.5, give_up=20)
                await send(clip)
                ended = self._now()
                await send(silence * 250)  # 5 s for the reply to start
                after = [t for t in self.loud if t > ended]
                self.result.replies_s.append(round(after[0] - ended, 2) if after else None)
            await wait_for_quiet(2.0, give_up=20)  # let the agent finish its last sentence
            listener.cancel()


async def _post(url: str, fmt: str, form: dict[str, str], get: bool = False) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15) as client:
        if get:
            return await client.get(_local(url), params=form)
        if fmt == "vonage":
            return await client.post(_local(url), json=form)
        return await client.post(_local(url), data=form)


def _form(fmt: str, sid: str, caller: str, ours: str, status: str | None = None) -> dict[str, str]:
    if fmt == "vonage":
        form = {"uuid": sid, "from": caller.lstrip("+"), "to": ours.lstrip("+")}
        return form | ({"status": status} if status else {})
    if fmt == "exotel":
        return {"CallSid": sid, "From": caller, "To": ours} | ({"Status": status} if status else {})
    return {"CallSid": sid, "From": caller, "To": ours} | ({"CallStatus": status} if status else {})


async def _call(
    fmt: str, answer_url: str, status_url: str, sid: str, caller: str, ours: str,
    clips: list[bytes], result: Result,
) -> None:  # fmt: skip
    try:
        answer = await _post(
            answer_url, fmt, _form(fmt, sid, caller, ours), get=fmt in ("vonage", "exotel")
        )
        answer.raise_for_status()
        await Customer(fmt, result).talk(_stream_url(fmt, answer.text), sid, clips)
        await _post(status_url, fmt, _form(fmt, sid, caller, ours, "completed"))
    except Exception as exc:
        result.failure = f"{exc.__class__.__name__}: {exc}"[:200]


def _cpu_seconds() -> float | None:
    """CPU time used by this container so far (cgroup v2), for the load figures."""
    try:
        stat = Path("/sys/fs/cgroup/cpu.stat").read_text()
    except OSError:
        return None
    usage = re.search(r"^usage_usec (\d+)", stat, re.M)
    return int(usage.group(1)) / 1e6 if usage else None


async def run(agent_name: str | None, calls: int, keep: bool) -> int:
    run_id = datetime.now(UTC).strftime("%m%d-%H%M%S")
    inbound_count = calls - calls // 2
    outbound_count = calls // 2
    redis = get_redis()
    await redis.delete(QUEUE_KEY)

    async with get_sessionmaker()() as db:
        query = select(Agent).where(
            Agent.published_version_id.is_not(None), Agent.status != AgentStatus.ARCHIVED
        )
        if agent_name:
            query = query.where(Agent.name == agent_name)
        agent = (await db.scalars(query.order_by(Agent.created_at))).first()
        if agent is None:
            print("No published agent found" + (f" named '{agent_name}'" if agent_name else ""))
            return 2
        keys = await load_ai_keys(db)
        published = await db.get(AgentVersion, agent.published_version_id)
        config = AgentConfig.model_validate(published.config if published else {})
        examples = {v.name: v.example for v in config.variables if v.example}
        variables = {
            name: examples.get(name) or SAMPLE_VALUES.get(name, "test")
            for name in detected_variables(config)
        }
        variables.pop("preferred_language", None)
        variables.pop("state", None)

        accounts: dict[str, ProviderAccount] = {}
        numbers: dict[str, PhoneNumber] = {}
        for i, fmt in enumerate(FORMATS):
            account = ProviderAccount(
                provider=TelephonyProvider.SIMULATOR,
                name=f"Simulator {run_id} · {fmt}",
                encrypted_credentials=encrypt_json({"format": fmt}),
                credential_hint=fmt,
                webhook_secret=new_webhook_secret(),
                max_concurrent_calls=100,
                verified_at=datetime.now(UTC),
            )
            db.add(account)
            await db.flush()
            number = PhoneNumber(
                provider_account_id=account.id,
                e164=f"+9180{int(time.time()) % 10_000_000:07d}{i}",
                label=f"Simulator {run_id}",
                inbound_agent_id=agent.id,
                max_concurrent_calls=100,
            )
            db.add(number)
            accounts[fmt], numbers[fmt] = account, number
        await db.flush()  # number ids, for the campaign
        campaign = None
        if outbound_count:
            campaign = Campaign(
                name=f"Simulation {run_id}",
                agent_id=agent.id,
                phone_number_ids=[str(n.id) for n in numbers.values()],
                status=CampaignStatus.RUNNING,
                days=list(range(7)),
                start_time="00:00",
                end_time="23:59",
                max_concurrent=outbound_count,
                max_attempts=1,
                started_at=datetime.now(UTC),
            )
            db.add(campaign)
            await db.flush()
            for i in range(outbound_count):
                db.add(
                    CampaignContact(
                        campaign_id=campaign.id,
                        phone=f"+91984500{i:04d}",
                        variables=variables,
                    )
                )
        await db.commit()
        urls = {fmt: webhook_urls(account) for fmt, account in accounts.items()}
        by_account = {str(a.id): fmt for fmt, a in accounts.items()}

    print(
        f"Simulating {calls} calls with '{agent.name}': {inbound_count} incoming, "
        f"{outbound_count} from a campaign (formats: {', '.join(FORMATS)})",
        flush=True,
    )
    clips = [await _speech(keys["sarvam"], text, lang) for text, lang in LINES]
    cpu_before, wall_before = _cpu_seconds(), time.monotonic()
    results: list[Result] = []
    tasks = []
    formats = list(FORMATS)
    for i in range(inbound_count):
        fmt = formats[i % len(formats)]
        result = Result(f"{fmt} incoming")
        results.append(result)
        sid = f"SIM-IN-{uuid.uuid4().hex[:12]}"
        tasks.append(
            _call(fmt, urls[fmt]["answer_url"], urls[fmt]["status_url"], sid,
                  f"+91984500{9000 + i}", numbers[fmt].e164, clips, result)
        )  # fmt: skip
    deadline = time.monotonic() + 60
    answered = 0
    while answered < outbound_count and time.monotonic() < deadline:
        item = await redis.lpop(QUEUE_KEY)
        if not item:
            await asyncio.sleep(0.3)
            continue
        dial = json.loads(str(item))
        fmt = dial["format"]
        result = Result(f"{fmt} campaign")
        results.append(result)
        answer_url = dial["answer_url"]
        tasks.append(
            _call(fmt, answer_url, dial["status_url"], dial["sid"], dial["to"], dial["from"],
                  clips, result)
        )  # fmt: skip
        answered += 1
    for missing in range(outbound_count - answered):
        results.append(Result(f"campaign call {missing + 1}", failure="the dialer never dialed"))
    await asyncio.gather(*tasks)
    cpu_after, wall = _cpu_seconds(), time.monotonic() - wall_before

    # Wait for the after-call analysis, then read what each call recorded.
    async with get_sessionmaker()() as db:
        for _ in range(45):
            pending = await db.scalar(
                select(func.count())
                .select_from(Call)
                .where(Call.provider_account_id.in_([a.id for a in accounts.values()]))
                .where(Call.outcome.is_(None))
            )
            if not pending:
                break
            await asyncio.sleep(2)
            db.expire_all()
        recorded = list(
            await db.scalars(
                select(Call).where(Call.provider_account_id.in_([a.id for a in accounts.values()]))
            )
        )
        errors = dict(
            (
                await db.execute(
                    select(CallEvent.call_id, func.count())
                    .where(
                        CallEvent.call_id.in_([c.id for c in recorded]),
                        CallEvent.type == CallEventType.ERROR,
                    )
                    .group_by(CallEvent.call_id)
                )
            ).all()
        )
    unmatched = {fmt: [c for c in recorded if by_account[str(c.provider_account_id)] == fmt]
                 for fmt in FORMATS}  # fmt: skip
    for result in results:
        fmt = result.label.split()[0]
        direction = "inbound" if "incoming" in result.label else "outbound"
        match = next((c for c in unmatched.get(fmt, []) if c.direction.value == direction), None)
        if match:
            unmatched[fmt].remove(match)
            result.call_id, result.outcome = match.id, match.outcome
            result.errors = int(errors.get(match.id, 0))

    print()
    print(f"{'call':<20}{'greeting':>10}{'replies':>16}  {'outcome':<22}result")
    for r in results:
        replies = ", ".join("—" if x is None else f"{x:.1f}s" for x in r.replies_s) or "—"
        greeting = "—" if r.greeting_s is None else f"{r.greeting_s:.1f}s"
        verdict = "ok" if r.ok else (r.failure or ("errors logged" if r.errors else "FAILED"))
        print(f"{r.label:<20}{greeting:>10}{replies:>16}  {(r.outcome or '—'):<22}{verdict}")
    passed = sum(r.ok for r in results)
    replies_all = sorted(x for r in results for x in r.replies_s if x is not None)
    print()
    print(f"{passed} of {len(results)} calls passed.")
    if replies_all:
        print(
            f"Replies: typical {replies_all[len(replies_all) // 2]:.1f}s, "
            f"slowest {replies_all[-1]:.1f}s."
        )
    if cpu_before is not None and cpu_after is not None:
        cores = (cpu_after - cpu_before) / wall
        print(
            f"API container CPU: {cores:.2f} cores on average for {len(results)} simultaneous "
            f"calls (~{cores / max(1, len(results)):.2f} per call; whole run {wall:.0f}s)."
        )

    if not keep:
        async with get_sessionmaker()() as db:
            ids = [a.id for a in accounts.values()]
            if campaign is not None:
                await db.execute(delete(Campaign).where(Campaign.id == campaign.id))
            await db.execute(delete(Call).where(Call.provider_account_id.in_(ids)))
            await db.execute(delete(PhoneNumber).where(PhoneNumber.provider_account_id.in_(ids)))
            await db.execute(delete(ProviderAccount).where(ProviderAccount.id.in_(ids)))
            await db.commit()
        print("Removed the simulator's accounts, numbers, campaign and calls (--keep keeps them).")
    return 0 if passed == len(results) else 1
