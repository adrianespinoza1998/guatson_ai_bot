"""Registers the six tools, validates their inputs against a strict JSON Schema
(derived from a pydantic model per tool), and dispatches calls from the agent loop.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.tools import artifacts as artifacts_tools
from app.tools import tasks as tasks_tools
from app.tools.base import ToolContext, ToolError

__all__ = ["ToolContext", "ToolError", "dispatch", "tool_definitions"]

ToolHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler


_SPECS = (
    ToolSpec(
        name="create_task",
        description="Crea una tarea nueva para el usuario.",
        input_model=tasks_tools.CreateTaskInput,
        handler=tasks_tools.create_task,
    ),
    ToolSpec(
        name="search_tasks",
        description="Busca tareas existentes por texto, estado y/o etiquetas.",
        input_model=tasks_tools.SearchTasksInput,
        handler=tasks_tools.search_tasks,
    ),
    ToolSpec(
        name="update_task",
        description="Edita, completa o cancela una tarea existente.",
        input_model=tasks_tools.UpdateTaskInput,
        handler=tasks_tools.update_task,
    ),
    ToolSpec(
        name="save_artifact",
        description=(
            "Guarda una pieza de código nueva o una nueva versión de un artefacto "
            "existente. El código debe estar completo y listo para ejecutar."
        ),
        input_model=artifacts_tools.SaveArtifactInput,
        handler=artifacts_tools.save_artifact,
    ),
    ToolSpec(
        name="get_artifact",
        description=(
            "Recupera el código de un artefacto: la última versión o una versión específica."
        ),
        input_model=artifacts_tools.GetArtifactInput,
        handler=artifacts_tools.get_artifact,
    ),
    ToolSpec(
        name="list_artifacts",
        description="Lista los artefactos guardados en este chat.",
        input_model=artifacts_tools.ListArtifactsInput,
        handler=artifacts_tools.list_artifacts,
    ),
)

TOOLS: dict[str, ToolSpec] = {spec.name: spec for spec in _SPECS}


def tool_definitions() -> list[dict[str, Any]]:
    """Tool schemas for the Anthropic API `tools` parameter. The last definition
    carries the `cache_control` breakpoint, caching the whole (stable) tools array
    alongside the system prompt — see docs/DECISIONS.md.
    """
    definitions: list[dict[str, Any]] = []
    for index, spec in enumerate(_SPECS):
        definition: dict[str, Any] = {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_model.model_json_schema(),
        }
        if index == len(_SPECS) - 1:
            definition["cache_control"] = {"type": "ephemeral"}
        definitions.append(definition)
    return definitions


async def dispatch(ctx: ToolContext, name: str, raw_input: dict[str, Any]) -> tuple[str, bool]:
    """Execute one tool call. Always returns (content, is_error) — never raises."""
    spec = TOOLS.get(name)
    if spec is None:
        return f"Herramienta desconocida: {name!r}", True

    try:
        validated = spec.input_model.model_validate(raw_input)
    except ValidationError as exc:
        return f"Entrada inválida para {name}: {exc.errors(include_url=False)}", True

    try:
        result = await spec.handler(ctx, validated.model_dump())
    except ToolError as exc:
        return str(exc), True

    return json.dumps(result, ensure_ascii=False, default=str), False
