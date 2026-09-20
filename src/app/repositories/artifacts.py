"""Persistence for `artifacts` and `artifact_versions`. Saving a new version of an
existing artifact never overwrites a prior one; `version` only increments.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactVersion


async def get_artifact_by_name(
    session: AsyncSession, *, chat_id: int, name: str
) -> Artifact | None:
    stmt = select(Artifact).where(Artifact.chat_id == chat_id, Artifact.name == name)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def save_artifact_version(
    session: AsyncSession,
    *,
    chat_id: int,
    name: str,
    language: str,
    code: str,
    description: str | None = None,
    message_id: int | None = None,
) -> tuple[Artifact, ArtifactVersion]:
    artifact = await get_artifact_by_name(session, chat_id=chat_id, name=name)
    if artifact is None:
        artifact = Artifact(chat_id=chat_id, name=name, language=language, description=description)
        session.add(artifact)
        await session.flush()
    else:
        artifact.language = language
        if description is not None:
            artifact.description = description

    max_version_stmt = select(func.max(ArtifactVersion.version)).where(
        ArtifactVersion.artifact_id == artifact.id
    )
    max_version = (await session.execute(max_version_stmt)).scalar_one()
    next_version = (max_version or 0) + 1

    version = ArtifactVersion(
        artifact_id=artifact.id,
        version=next_version,
        code=code,
        message_id=message_id,
    )
    session.add(version)
    await session.flush()
    return artifact, version


async def get_artifact_version(
    session: AsyncSession, *, chat_id: int, name: str, version: int | None = None
) -> tuple[Artifact, ArtifactVersion] | None:
    artifact = await get_artifact_by_name(session, chat_id=chat_id, name=name)
    if artifact is None:
        return None

    stmt = select(ArtifactVersion).where(ArtifactVersion.artifact_id == artifact.id)
    if version is None:
        stmt = stmt.order_by(ArtifactVersion.version.desc()).limit(1)
    else:
        stmt = stmt.where(ArtifactVersion.version == version)

    result = await session.execute(stmt)
    artifact_version = result.scalar_one_or_none()
    if artifact_version is None:
        return None
    return artifact, artifact_version


async def list_artifacts(session: AsyncSession, *, chat_id: int, limit: int = 20) -> list[Artifact]:
    stmt = (
        select(Artifact)
        .where(Artifact.chat_id == chat_id)
        .order_by(Artifact.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_versions(session: AsyncSession, *, chat_id: int, name: str) -> list[ArtifactVersion]:
    """All versions of one artifact, newest first. Empty if the artifact doesn't exist
    (or belongs to a different chat) — used by the dashboard, where a bad `name` in the
    URL should just render an empty table, not raise.
    """
    artifact = await get_artifact_by_name(session, chat_id=chat_id, name=name)
    if artifact is None:
        return []
    stmt = (
        select(ArtifactVersion)
        .where(ArtifactVersion.artifact_id == artifact.id)
        .order_by(ArtifactVersion.version.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
