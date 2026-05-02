from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docvault.exceptions import SummarizationError


def _make_tool_response(summary: str, keywords: list[str]) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = "set_document_metadata"
    block.input = {"summary": summary, "keywords": keywords}
    response = MagicMock()
    response.content = [block]
    return response


def _make_non_tool_response() -> MagicMock:
    block = MagicMock()
    block.type = "text"
    response = MagicMock()
    response.content = [block]
    return response


class TestDocumentSummarizer:
    def test_import_error_raises_summarization_error(self, monkeypatch):
        import sys

        # Temporarily hide the anthropic package
        real_anthropic = sys.modules.get("anthropic")
        sys.modules["anthropic"] = None  # type: ignore[assignment]
        try:
            import importlib
            import docvault.core.summarizer as mod

            importlib.reload(mod)
            with pytest.raises(SummarizationError, match="anthropic"):
                mod.DocumentSummarizer(api_key="test-key")
        finally:
            if real_anthropic is not None:
                sys.modules["anthropic"] = real_anthropic
            else:
                del sys.modules["anthropic"]
            importlib.reload(mod)

    def test_successful_infer_metadata(self):
        from docvault.core.summarizer import DocumentSummarizer

        mock_response = _make_tool_response(
            "A configuration document for a web service.",
            ["config", "web", "service"],
        )
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            summarizer = DocumentSummarizer(
                api_key="sk-test", model="claude-haiku-4-5-20251001"
            )

        import asyncio

        summary, keywords = asyncio.run(
            summarizer.infer_metadata({"host": "localhost", "port": 8080})
        )
        assert summary == "A configuration document for a web service."
        assert keywords == ["config", "web", "service"]

    def test_content_truncation(self):
        from docvault.core.summarizer import _MAX_CONTENT_CHARS, DocumentSummarizer

        captured_messages: list = []

        async def capture_create(**kwargs):
            captured_messages.append(kwargs["messages"])
            return _make_tool_response("summary", ["kw"])

        mock_client = MagicMock()
        mock_client.messages.create = capture_create

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            summarizer = DocumentSummarizer(api_key="sk-test")

        # Content larger than _MAX_CONTENT_CHARS
        large_content = {"data": "x" * (_MAX_CONTENT_CHARS + 5000)}

        import asyncio

        asyncio.run(summarizer.infer_metadata(large_content))

        sent_text = captured_messages[0][0]["content"]
        assert "[truncated]" in sent_text
        # Rough check: the message shouldn't be much longer than the limit
        assert len(sent_text) < _MAX_CONTENT_CHARS + 500

    def test_api_failure_raises_summarization_error(self):
        from docvault.core.summarizer import DocumentSummarizer

        async def fail_create(**kwargs):
            raise ConnectionError("Network unreachable")

        mock_client = MagicMock()
        mock_client.messages.create = fail_create

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            summarizer = DocumentSummarizer(api_key="sk-test")

        import asyncio

        with pytest.raises(SummarizationError, match="LLM call failed"):
            asyncio.run(summarizer.infer_metadata({"key": "val"}))

    def test_no_tool_call_in_response_raises(self):
        from docvault.core.summarizer import DocumentSummarizer

        mock_response = _make_non_tool_response()
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            summarizer = DocumentSummarizer(api_key="sk-test")

        import asyncio

        with pytest.raises(
            SummarizationError, match="did not return expected tool call"
        ):
            asyncio.run(summarizer.infer_metadata({"key": "val"}))

    def test_custom_model_passed_to_client(self):
        from docvault.core.summarizer import DocumentSummarizer

        calls: list[dict] = []

        async def capture_create(**kwargs):
            calls.append(kwargs)
            return _make_tool_response("s", ["k"])

        mock_client = MagicMock()
        mock_client.messages.create = capture_create

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            summarizer = DocumentSummarizer(api_key="sk-test", model="claude-opus-4")

        import asyncio

        asyncio.run(summarizer.infer_metadata({"x": 1}))
        assert calls[0]["model"] == "claude-opus-4"

    def test_empty_content_does_not_crash(self):
        from docvault.core.summarizer import DocumentSummarizer

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_tool_response("Empty document.", [])
        )

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            summarizer = DocumentSummarizer(api_key="sk-test")

        import asyncio

        summary, keywords = asyncio.run(summarizer.infer_metadata({}))
        assert isinstance(summary, str)
        assert isinstance(keywords, list)

    def test_keywords_returned_as_list(self):
        from docvault.core.summarizer import DocumentSummarizer

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_tool_response("A doc.", ["a", "b", "c"])
        )

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            summarizer = DocumentSummarizer(api_key="sk-test")

        import asyncio

        _, keywords = asyncio.run(summarizer.infer_metadata({"v": 1}))
        assert keywords == ["a", "b", "c"]
        assert isinstance(keywords, list)
