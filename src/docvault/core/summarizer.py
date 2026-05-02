from __future__ import annotations

import json
from typing import Any

from ..exceptions import SummarizationError

try:
    import anthropic

    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False

_SYSTEM_PROMPT = (
    "You are a document metadata expert. "
    "You analyze JSON documents and extract precise, concise metadata. "
    "Never hallucinate; base everything strictly on the document content."
)

_TOOL = {
    "name": "set_document_metadata",
    "description": "Record inferred metadata for a JSON document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Concise 1-2 sentence description of what the document represents.",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-8 relevant keywords or topic tags derived from the content.",
            },
        },
        "required": ["summary", "keywords"],
    },
}

_MAX_CONTENT_CHARS = 8_000


class DocumentSummarizer:
    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001") -> None:
        if not _HAS_ANTHROPIC:
            raise SummarizationError(
                "LLM summarization requires the 'anthropic' package: uv add 'docvault[llm]'"
            )
        self._client: anthropic.AsyncAnthropic = anthropic.AsyncAnthropic(
            api_key=api_key
        )
        self._model = model

    async def infer_metadata(self, content: dict[str, Any]) -> tuple[str, list[str]]:
        content_str = json.dumps(content, indent=2, default=str)
        if len(content_str) > _MAX_CONTENT_CHARS:
            content_str = content_str[:_MAX_CONTENT_CHARS] + "\n... [truncated]"

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=512,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        # Cache the system prompt across repeated calls.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=[_TOOL],
                tool_choice={"type": "tool", "name": "set_document_metadata"},
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Analyze this JSON document and infer its metadata:\n\n{content_str}"
                        ),
                    }
                ],
            )
        except Exception as exc:
            raise SummarizationError(f"LLM call failed: {exc}") from exc

        for block in response.content:
            if block.type == "tool_use" and block.name == "set_document_metadata":
                inp = block.input
                return str(inp["summary"]), list(inp["keywords"])

        raise SummarizationError("LLM did not return expected tool call")
