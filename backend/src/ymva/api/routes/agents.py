import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ymva.api.deps import AdminUser, CurrentUser, DbSession, client_ip
from ymva.models import Agent, AgentStatus, AgentVersion, User
from ymva.services import audit
from ymva.voice.config import GEMINI_LIVE_VOICES, SARVAM_VOICES, AgentConfig
from ymva.voice.languages import LANGUAGES
from ymva.voice.templates import TEMPLATES

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


class AgentDetail(AgentSummary):
    config: dict[str, Any]
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
    status: Literal["active", "paused", "archived"] | None = None


class VersionIn(BaseModel):
    config: AgentConfig
    note: str = Field(default="", max_length=500)


class PublishIn(BaseModel):
    version_id: uuid.UUID


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
        "has_unpublished_changes": published is None or published.id != latest.id,
        "updated_at": max(agent.updated_at, latest.created_at),
    }


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
        config=latest.config,
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
        "llm_models": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.6-flash"],
        "templates": [{"id": k, "name": v[0]} for k, v in TEMPLATES.items()],
    }


@router.get("", response_model=list[AgentSummary])
async def list_agents(_: CurrentUser, db: DbSession) -> list[AgentSummary]:
    agents = list(
        await db.scalars(
            select(Agent).where(Agent.status != AgentStatus.ARCHIVED).order_by(Agent.created_at)
        )
    )
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
    body: AgentCreateIn, request: Request, admin: AdminUser, db: DbSession
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


@router.get("/{agent_id}", response_model=AgentDetail)
async def get_agent(agent_id: uuid.UUID, _: CurrentUser, db: DbSession) -> AgentDetail:
    return await _detail(db, await _get_agent(db, agent_id))


@router.get("/{agent_id}/versions/{version_id}")
async def get_version(
    agent_id: uuid.UUID, version_id: uuid.UUID, _: CurrentUser, db: DbSession
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
    agent_id: uuid.UUID, body: AgentPatchIn, request: Request, admin: AdminUser, db: DbSession
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


@router.post(
    "/{agent_id}/versions", response_model=AgentDetail, status_code=status.HTTP_201_CREATED
)
async def save_version(
    agent_id: uuid.UUID, body: VersionIn, request: Request, admin: AdminUser, db: DbSession
) -> AgentDetail:
    agent = await _get_agent(db, agent_id)
    latest = await db.scalar(
        select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == agent.id)
    )
    number = (latest or 0) + 1
    db.add(
        AgentVersion(
            agent_id=agent.id,
            version=number,
            config=body.config.model_dump(mode="json"),
            note=body.note.strip(),
            source="manual",
            created_by_id=admin.id,
        )
    )
    await audit.record(
        db,
        actor=admin,
        action="agent.version",
        entity_type="agent",
        entity_id=agent.id,
        summary=f"Saved version {number} of '{agent.name}'",
        changes={"note": body.note.strip()} if body.note.strip() else {},
        ip_address=client_ip(request),
    )
    await db.commit()
    return await _detail(db, agent)


@router.post("/{agent_id}/publish", response_model=AgentDetail)
async def publish(
    agent_id: uuid.UUID, body: PublishIn, request: Request, admin: AdminUser, db: DbSession
) -> AgentDetail:
    agent = await _get_agent(db, agent_id)
    version = await db.get(AgentVersion, body.version_id)
    if version is None or version.agent_id != agent.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
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
