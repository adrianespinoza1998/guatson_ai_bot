"""Thin wrapper around the Anthropic SDK: fixes model/max_tokens/system/tools so
callers only ever pass the growing `messages` array.
"""

from __future__ import annotations

from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage
from anthropic.types import MessageParam, ToolParam
from anthropic.types.text_block_param import TextBlockParam

from app.config import Settings
from app.llm.prompts import build_system_blocks
from app.tools.registry import tool_definitions


class ClaudeClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def create_message(self, messages: list[dict[str, Any]]) -> AnthropicMessage:
        # `messages`, the system blocks, and the tool definitions are all built by our
        # own code as plain dicts matching the API's JSON shape exactly; the SDK's
        # TypedDicts add no runtime value here, so we cast rather than re-type every
        # content block through them.
        return await self._client.messages.create(
            model=self._settings.claude_model,
            max_tokens=self._settings.claude_max_tokens,
            system=cast(list[TextBlockParam], build_system_blocks()),
            tools=cast(list[ToolParam], tool_definitions()),
            messages=cast(list[MessageParam], messages),
        )

    async def aclose(self) -> None:
        await self._client.close()
