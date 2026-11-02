import io
import uuid
import wave
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from ymva.api.deps import AgentEditor, AgentOpener, AgentViewer, CurrentUser, DbSession, client_ip
from ymva.models import Agent, AgentStatus, AgentVersion, Call, PhoneNumber, User
from ymva.services import audit
from ymva.services.integrations import load_ai_keys
from ymva.voice import greeting
from ymva.voice.config import (
    GEMINI_LIVE_MODELS,
    GEMINI_LIVE_VOICES,
    LLM_MODELS,
    SARVAM_VOICES,
    AgentConfig,
    Variable,
    call_details,
)
from ymva.voice.languages import LANGUAGES
from ymva.voice.templates import TEMPLATES, VOICE_PREVIEW_LINES

router = APIRouter(prefix="/agents", tags=["agents"])


class VersionSummary(BaseModel):
    id: uuid.UUID
    version: int
    note: str
    source: str
    created_at: datetime
    created_by: str | None


class AgentSummary(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    status: AgentStatus
    mode: str
    languages: list[str]
    latest_version: int
    published_version: int | None
    has_unpublished_changes: bool
    updated_at: datetime
    # Per-call details an app starting a call must send (the agent's {{variables}}).
    details: list[Variable] = []


class AgentDetail(AgentSummary):
    # What the editor shows: the saved draft if there is one, otherwise the latest version.
    config: dict[str, Any]
    has_draft_changes: bool
    draft_updated_at: datetime | None
    latest_version_id: uuid.UUID
    published_version_id: uuid.UUID | None
    versions: list[VersionSummary]


class AgentCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    template: Literal["interest_reminder", "blank"] = "interest_reminder"


class AgentPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    status: Literal["draft", "active", "paused", "archived"] | None = None


class DraftIn(BaseModel):
    config: AgentConfig


class VersionIn(BaseModel):
    note: str = Field(default="", max_length=500)
    # Defaults to the current draft.
    config: AgentConfig | None = None


class PublishIn(BaseModel):
    # A saved version to make live; omit to publish the current draft.
    version_id: uuid.UUID | None = None
    note: str = Field(default="", max_length=500)


async def _versions(db: DbSession, agent_id: uuid.UUID) -> list[AgentVersion]:
    return list(
        await db.scalars(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent_id)
            .order_by(AgentVersion.version.desc())
        )
    )


def _summary(agent: Agent, latest: AgentVersion, published: AgentVersion | None) -> dict[str, Any]:
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "status": agent.status,
        "mode": latest.config.get("mode", "pipeline"),
        "languages": latest.config.get("languages", []),
        "latest_version": latest.version,
        "published_version": published.version if published else None,
        "has_unpublished_changes": published is None
        or published.id != latest.id
        or _draft_differs(agent, latest),
        "updated_at": max(agent.updated_at, latest.created_at),
        # Calls from apps use the published version.
        "details": call_details(AgentConfig.model_validate((published or latest).config)),
    }


def _draft_differs(agent: Agent, latest: AgentVersion) -> bool:
    return agent.draft_config is not None and agent.draft_config != latest.config


async def _latest(db: DbSession, agent_id: uuid.UUID) -> AgentVersion:
    latest = await db.scalar(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_id)
        .order_by(AgentVersion.version.desc())
        .limit(1)
    )
    assert latest is not None  # every agent is created with version 1
    return latest


async def _new_version(
    db: DbSession, agent: Agent, config: dict[str, Any], note: str, source: str, admin: User
) -> AgentVersion:
    latest = await _latest(db, agent.id)
    if config == latest.config:
        return latest
    version = AgentVersion(
        agent_id=agent.id,
        version=latest.version + 1,
        config=config,
        note=note.strip(),
        source=source,
        created_by_id=admin.id,
    )
    db.add(version)
    agent.draft_config = None  # the draft is now this version
    await db.flush()
    return version


def _current(config: dict[str, Any]) -> dict[str, Any]:
    """Show stored configs with today's defaults filled in and retired models replaced."""
    try:
        return AgentConfig.model_validate(config).model_dump(mode="json")
    except ValueError:
        return config


async def _get_agent(db: DbSession, agent_id: uuid.UUID) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    return agent


async def _detail(db: DbSession, agent: Agent) -> AgentDetail:
    await db.refresh(agent)  # updated_at is set by the database on update
    versions = await _versions(db, agent.id)
    latest = versions[0]
    published = next((v for v in versions if v.id == agent.published_version_id), None)
    authors = {
        u.id: u.email
        for u in await db.scalars(
            select(User).where(User.id.in_({v.created_by_id for v in versions if v.created_by_id}))
        )
    }
    return AgentDetail(
        **_summary(agent, latest, published),
        config=_current(agent.draft_config if agent.draft_config is not None else latest.config),
        has_draft_changes=_draft_differs(agent, latest),
        draft_updated_at=agent.draft_updated_at,
        latest_version_id=latest.id,
        published_version_id=agent.published_version_id,
        versions=[
            VersionSummary(
                id=v.id,
                version=v.version,
                note=v.note,
                source=v.source,
                created_at=v.created_at,
                created_by=authors.get(v.created_by_id) if v.created_by_id else None,
            )
            for v in versions
        ],
    )


@router.get("/options")
async def options(_: CurrentUser) -> dict[str, Any]:
    """Choices the agent editor offers."""
    return {
        "languages": [
            {"code": i.code, "name": i.name, "native": i.native} for i in LANGUAGES.values()
        ],
        "sarvam_voices": list(SARVAM_VOICES),
        "gemini_live_voices": list(GEMINI_LIVE_VOICES),
        "llm_models": list(LLM_MODELS),
        "gemini_live_models": list(GEMINI_LIVE_MODELS),
        "templates": [{"id": k, "name": v[0]} for k, v in TEMPLATES.items()],
    }


@router.get("", response_model=list[AgentSummary])
async def list_agents(_: CurrentUser, db: DbSession, archived: bool = False) -> list[AgentSummary]:
    status_filter = (
        Agent.status == AgentStatus.ARCHIVED if archived else Agent.status != AgentStatus.ARCHIVED
    )
    agents = list(await db.scalars(select(Agent).where(status_filter).order_by(Agent.created_at)))
    if not agents:
        return []
    latest_numbers = (
        select(AgentVersion.agent_id, func.max(AgentVersion.version).label("v"))
        .group_by(AgentVersion.agent_id)
        .subquery()
    )
    latest_rows = await db.scalars(
        select(AgentVersion).join(
            latest_numbers,
            (AgentVersion.agent_id == latest_numbers.c.agent_id)
            & (AgentVersion.version == latest_numbers.c.v),
        )
    )
    latest = {v.agent_id: v for v in latest_rows}
    published_ids = [a.published_version_id for a in agents if a.published_version_id]
    published = {
        v.id: v
        for v in await db.scalars(select(AgentVersion).where(AgentVersion.id.in_(published_ids)))
    }
    return [
        AgentSummary(
            **_summary(
                a,
                latest[a.id],
                published.get(a.published_version_id) if a.published_version_id else None,
            )
        )
        for a in agents
        if a.id in latest
    ]


@router.post("", response_model=AgentDetail, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreateIn, request: Request, admin: AgentEditor, db: DbSession
) -> AgentDetail:
    config = TEMPLATES[body.template][1]()
    agent = Agent(name=body.name.strip(), description=body.description, created_by_id=admin.id)
    db.add(agent)
    await db.flush()
    db.add(
        AgentVersion(
            agent_id=agent.id,
            version=1,
            config=config.model_dump(mode="json"),
            note=f"Created from template: {TEMPLATES[body.template][0]}",
            source="template",
            created_by_id=admin.id,
        )
    )
    await audit.record(
        db,
        actor=admin,
        action="agent.create",
        entity_type="agent",
        entity_id=agent.id,
        summary=f"Created agent '{agent.name}'",
        ip_address=client_ip(request),
    )
    await db.commit()
    return await _detail(db, agent)


class VoicePreviewIn(BaseModel):
    engine: Literal["sarvam", "gemini"]
    voice: str = Field(max_length=40)
    language: str = Field(default="en", max_length=8)


_preview_cache: dict[tuple[str, str, str], bytes] = {}


@router.post("/voice-preview")
async def voice_preview(body: VoicePreviewIn, _: AgentEditor, db: DbSession) -> Response:
    """A short sample of a voice, so you can choose by ear. Cached after the first play."""
    voices = SARVAM_VOICES if body.engine == "sarvam" else GEMINI_LIVE_VOICES
    if body.voice not in voices or body.language not in LANGUAGES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown voice or language")
    key = (body.engine, body.voice, body.language)
    if key not in _preview_cache:
        keys = await load_ai_keys(db)
        provider = "sarvam" if body.engine == "sarvam" else "google"
        if provider not in keys:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Add the {provider} API key in Integrations first"
            )
        line = VOICE_PREVIEW_LINES.get(body.language, VOICE_PREVIEW_LINES["en"]).format(
            name=body.voice.capitalize()
        )
        try:
            if body.engine == "sarvam":
                pcm, rate = await greeting.synthesize_sarvam(
                    line, body.language, body.voice, 1.0, 24000, keys["sarvam"]
                )
            else:
                pcm, rate = await greeting.synthesize_gemini(line, body.voice, keys["google"])
        except Exception as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "The voice provider didn't return a sample. Try again."
            ) from exc
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        _preview_cache[key] = buffer.getvalue()
    return Response(
        _preview_cache[key],
        media_type="audio/wav",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/{agent_id}", response_model=AgentDetail)
async def get_agent(agent_id: uuid.UUID, _: AgentOpener, db: DbSession) -> AgentDetail:
    return await _detail(db, await _get_agent(db, agent_id))


@router.get("/{agent_id}/versions/{version_id}")
async def get_version(
    agent_id: uuid.UUID, version_id: uuid.UUID, _: AgentViewer, db: DbSession
) -> dict[str, Any]:
    version = await db.get(AgentVersion, version_id)
    if version is None or version.agent_id != agent_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    return {
        "id": version.id,
        "version": version.version,
        "note": version.note,
        "config": version.config,
    }


@router.patch("/{agent_id}", response_model=AgentDetail)
async def update_agent(
    agent_id: uuid.UUID, body: AgentPatchIn, request: Request, admin: AgentEditor, db: DbSession
) -> AgentDetail:
    agent = await _get_agent(db, agent_id)
    changes: dict[str, object] = {}
    if body.name is not None and body.name.strip() != agent.name:
        changes["name"] = [agent.name, body.name.strip()]
        agent.name = body.name.strip()
    if body.description is not None and body.description != agent.description:
        changes["description"] = "updated"
        agent.description = body.description
    if body.status is not None:
        if body.status == "archived":
            # Archived agents stop answering numbers.
            await db.execute(
                update(PhoneNumber)
                .where(PhoneNumber.inbound_agent_id == agent.id)
                .values(inbound_agent_id=None)
            )
        if body.status == "draft" and agent.published_version_id is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "A published agent can't be a draft")
        if body.status == "active" and agent.published_version_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Publish a version before activating")
        changes["status"] = [agent.status.value, body.status]
        agent.status = AgentStatus(body.status)
    if changes:
        await audit.record(
            db,
            actor=admin,
            action="agent.update",
            entity_type="agent",
            entity_id=agent.id,
            summary=f"Updated agent '{agent.name}'",
            changes=changes,
            ip_address=client_ip(request),
        )
    await db.commit()
    return await _detail(db, agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID, request: Request, admin: AgentEditor, db: DbSession
) -> None:
    """Delete an agent and its versions. Its calls stay in history under its name; numbers it
    answered are left without an inbound agent."""
    agent = await _get_agent(db, agent_id)
    if agent.status != AgentStatus.ARCHIVED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Archive the agent before deleting it")
    await db.execute(update(Call).where(Call.agent_id == agent.id).values(agent_name=agent.name))
    await audit.record(
        db,
        actor=admin,
        action="agent.delete",
        entity_type="agent",
        entity_id=agent.id,
        summary=f"Deleted agent '{agent.name}'",
        ip_address=client_ip(request),
    )
    agent.published_version_id = None
    await db.flush()
    await db.delete(agent)
    await db.commit()


@router.put("/{agent_id}/draft", response_model=AgentDetail)
async def save_draft(
    agent_id: uuid.UUID, body: DraftIn, request: Request, admin: AgentEditor, db: DbSession
) -> AgentDetail:
    """Save editor changes without creating a version."""
    agent = await _get_agent(db, agent_id)
    latest = await _latest(db, agent.id)
    config = body.config.model_dump(mode="json")
    before = agent.draft_config if agent.draft_config is not None else latest.config
    changed = sorted(k for k in config if config.get(k) != before.get(k))
    agent.draft_config = None if config == latest.config else config
    agent.draft_updated_at = datetime.now(UTC)
    if changed:
        await audit.record(
            db,
            actor=admin,
            action="agent.draft_saved",
            entity_type="agent",
            entity_id=agent.id,
            summary=f"Edited '{agent.name}'",
            changes={"sections": changed},
            ip_address=client_ip(request),
        )
    await db.commit()
    return await _detail(db, agent)


@router.delete("/{agent_id}/draft", response_model=AgentDetail)
async def discard_draft(
    agent_id: uuid.UUID, request: Request, admin: AgentEditor, db: DbSession
) -> AgentDetail:
    agent = await _get_agent(db, agent_id)
    if agent.draft_config is not None:
        agent.draft_config = None
        await audit.record(
            db,
            actor=admin,
            action="agent.draft_discarded",
            entity_type="agent",
            entity_id=agent.id,
            summary=f"Discarded unsaved edits to '{agent.name}'",
            ip_address=client_ip(request),
        )
    await db.commit()
    return await _detail(db, agent)


@router.post(
    "/{agent_id}/versions", response_model=AgentDetail, status_code=status.HTTP_201_CREATED
)
async def save_version(
    agent_id: uuid.UUID, body: VersionIn, request: Request, admin: AgentEditor, db: DbSession
) -> AgentDetail:
    """Snapshot the draft (or the given config) as a new numbered version."""
    agent = await _get_agent(db, agent_id)
    latest = await _latest(db, agent.id)
    config = (
        body.config.model_dump(mode="json")
        if body.config is not None
        else agent.draft_config
        if agent.draft_config is not None
        else latest.config
    )
    if config == latest.config:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Nothing changed since version {latest.version}"
        )
    version = await _new_version(db, agent, config, body.note, "manual", admin)
    await audit.record(
        db,
        actor=admin,
        action="agent.version",
        entity_type="agent",
        entity_id=agent.id,
        summary=f"Saved version {version.version} of '{agent.name}'",
        changes={"note": body.note.strip()} if body.note.strip() else {},
        ip_address=client_ip(request),
    )
    await db.commit()
    return await _detail(db, agent)


@router.post("/{agent_id}/versions/{version_id}/restore", response_model=AgentDetail)
async def restore_version(
    agent_id: uuid.UUID, version_id: uuid.UUID, request: Request, admin: AgentEditor, db: DbSession
) -> AgentDetail:
    """Load an older version into the editor as the draft."""
    agent = await _get_agent(db, agent_id)
    version = await db.get(AgentVersion, version_id)
    if version is None or version.agent_id != agent.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    latest = await _latest(db, agent.id)
    agent.draft_config = None if version.config == latest.config else version.config
    agent.draft_updated_at = datetime.now(UTC)
    await audit.record(
        db,
        actor=admin,
        action="agent.version_restored",
        entity_type="agent",
        entity_id=agent.id,
        summary=f"Loaded version {version.version} of '{agent.name}' into the editor",
        ip_address=client_ip(request),
    )
    await db.commit()
    return await _detail(db, agent)


@router.post("/{agent_id}/publish", response_model=AgentDetail)
async def publish(
    agent_id: uuid.UUID, body: PublishIn, request: Request, admin: AgentEditor, db: DbSession
) -> AgentDetail:
    """Make a saved version live, or (without version_id) save the draft as a version and
    make that live."""
    agent = await _get_agent(db, agent_id)
    if body.version_id is not None:
        found = await db.get(AgentVersion, body.version_id)
        if found is None or found.agent_id != agent.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
        version = found
    else:
        latest = await _latest(db, agent.id)
        config = agent.draft_config if agent.draft_config is not None else latest.config
        version = await _new_version(
            db, agent, config, body.note or "Published from the editor", "manual", admin
        )
    AgentConfig.model_validate(version.config)  # never publish something that can't run
    agent.published_version_id = version.id
    if agent.status in (AgentStatus.DRAFT, AgentStatus.PAUSED):
        agent.status = AgentStatus.ACTIVE
    await audit.record(
        db,
        actor=admin,
        action="agent.publish",
        entity_type="agent",
        entity_id=agent.id,
        summary=f"Published version {version.version} of '{agent.name}'",
        ip_address=client_ip(request),
    )
    await db.commit()
    return await _detail(db, agent)
