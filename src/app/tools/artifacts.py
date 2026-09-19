"""`save_artifact`, `get_artifact`, `list_artifacts` tool implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from app.repositories import artifacts as artifacts_repo
from app.tools.base import ToolError

if TYPE_CHECKING:
    from app.models import Artifact, ArtifactVersion
    from app.tools.base import ToolContext


def _artifact_to_dict(artifact: Artifact, version: ArtifactVersion) -> dict[str, Any]:
    return {
        "name": artifact.name,
        "language": artifact.language,
        "description": artifact.description,
        "version": version.version,
        "code": version.code,
        "created_at": version.created_at.isoformat(),
    }


class SaveArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    language: str = Field(min_length=1, max_length=50)
    code: str = Field(min_length=1)
    description: str | None = Field(default=None, max_length=2000)


async def save_artifact(ctx: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
    payload = SaveArtifactInput.model_validate(data)

    code_bytes = len(payload.code.encode("utf-8"))
    if code_bytes > ctx.settings.artifact_max_code_bytes:
        raise ToolError(
            f"El código supera el límite de {ctx.settings.artifact_max_code_bytes} bytes "
            f"({code_bytes} bytes). Divide el artefacto en piezas más pequeñas."
        )

    artifact, version = await artifacts_repo.save_artifact_version(
        ctx.session,
        chat_id=ctx.chat_id,
        name=payload.name,
        language=payload.language,
        code=payload.code,
        description=payload.description,
        message_id=ctx.assistant_message_id,
    )
    return _artifact_to_dict(artifact, version)


class GetArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    version: int | None = Field(default=None, ge=1)


async def get_artifact(ctx: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
    payload = GetArtifactInput.model_validate(data)
    found = await artifacts_repo.get_artifact_version(
        ctx.session, chat_id=ctx.chat_id, name=payload.name, version=payload.version
    )
    if found is None:
        raise ToolError(f"No existe el artefacto {payload.name!r} en este chat.")
    artifact, version = found
    return _artifact_to_dict(artifact, version)


class ListArtifactsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=50)


async def list_artifacts(ctx: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
    payload = ListArtifactsInput.model_validate(data)
    results = await artifacts_repo.list_artifacts(
        ctx.session, chat_id=ctx.chat_id, limit=payload.limit
    )
    return {
        "artifacts": [
            {
                "name": artifact.name,
                "language": artifact.language,
                "description": artifact.description,
                "created_at": artifact.created_at.isoformat(),
            }
            for artifact in results
        ],
        "count": len(results),
    }
