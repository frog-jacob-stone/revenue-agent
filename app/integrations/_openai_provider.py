"""OpenAI adapter for the LLM dispatcher.

The single place in the codebase that imports from the `openai` SDK. The
dispatcher in `app/integrations/llm.py` is the public surface; this file is
private to it (leading underscore).
"""
from __future__ import annotations

from typing import Any, AsyncIterator

import openai

from app.config import settings
from app.integrations.llm import LlmResponse, StreamDelta, ToolCall

_client: openai.AsyncOpenAI | None = None


class OpenAiProvider:
    """Implements `LlmProvider` against the OpenAI chat completions API."""

    name: str = "openai"

    async def complete(self, request: dict[str, Any]) -> LlmResponse:
        client = _get_client()
        kwargs = _to_openai_kwargs(request)
        completion = await client.chat.completions.create(**kwargs)
        choice = completion.choices[0] if completion.choices else None
        message = choice.message if choice else None
        usage = completion.usage

        tool_calls: list[ToolCall] = []
        if message and getattr(message, "tool_calls", None):
            for tc in message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments or "",
                    )
                )

        return LlmResponse(
            text=(message.content if message else "") or "",
            tool_calls=tool_calls,
            finish_reason=(choice.finish_reason if choice else None),
            prompt_tokens=(usage.prompt_tokens if usage else None),
            completion_tokens=(usage.completion_tokens if usage else None),
        )

    async def stream(
        self, request: dict[str, Any]
    ) -> AsyncIterator[StreamDelta | LlmResponse]:
        client = _get_client()
        kwargs = _to_openai_kwargs(request)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        text_buf: list[str] = []
        # Per-index tool-call buffers, reconstructed for the terminal LlmResponse.
        tool_calls_buf: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: Any = None

        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

            if delta.content:
                text_buf.append(delta.content)
                yield StreamDelta(text=delta.content)

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    slot = tool_calls_buf.setdefault(
                        idx, {"id": "", "name": "", "arguments": ""}
                    )
                    name_delta = ""
                    args_delta = ""
                    if tc_delta.id:
                        slot["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            name_delta = tc_delta.function.name
                            slot["name"] += name_delta
                        if tc_delta.function.arguments:
                            args_delta = tc_delta.function.arguments
                            slot["arguments"] += args_delta
                    yield StreamDelta(
                        tool_call_index=idx,
                        tool_call_id=tc_delta.id or None,
                        tool_call_name_delta=name_delta or None,
                        tool_call_args_delta=args_delta or None,
                    )

            if choice.finish_reason:
                finish_reason = choice.finish_reason

        yield LlmResponse(
            text="".join(text_buf),
            tool_calls=[
                ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                for tc in tool_calls_buf.values()
            ],
            finish_reason=finish_reason,
            prompt_tokens=(getattr(usage, "prompt_tokens", None) if usage else None),
            completion_tokens=(getattr(usage, "completion_tokens", None) if usage else None),
        )


def _get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


def _to_openai_kwargs(request: dict[str, Any]) -> dict[str, Any]:
    """Translate the dispatcher's request dict into OpenAI SDK kwargs.

    `stream` is set by the streaming caller; absent for non-streaming.
    """
    kwargs: dict[str, Any] = {"model": request["model"], "messages": request["messages"]}
    if request.get("tools") is not None:
        kwargs["tools"] = request["tools"]
    if request.get("response_format") is not None:
        kwargs["response_format"] = request["response_format"]
    if request.get("max_tokens") is not None:
        kwargs["max_tokens"] = request["max_tokens"]
    return kwargs
