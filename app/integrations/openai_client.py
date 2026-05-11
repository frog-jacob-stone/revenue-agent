"""Instrumented OpenAI client.

Every chat completion in this codebase goes through `call_openai_chat`. The
wrapper captures the full request and response, token usage, latency, and the
originating agent / workflow / purpose context (threaded in by callers via
`app.services.llm_logging.with_llm_context`) and writes a row to `llm_calls`.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import openai
from openai.types.chat import ChatCompletion

from app.config import settings
from app.services.llm_logging import fire_and_forget_write

logger = logging.getLogger(__name__)

_client: openai.AsyncOpenAI | None = None


def get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


def _serialize_message(msg: Any) -> dict[str, Any]:
    """Serialize a ChatCompletionMessage into a JSON-safe dict for the log."""
    out: dict[str, Any] = {
        "role": getattr(msg, "role", None),
        "content": getattr(msg, "content", None),
    }
    tool_calls = getattr(msg, "tool_calls", None) or []
    if tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": getattr(tc, "type", "function"),
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]
    return out


def _serialize_response(completion: ChatCompletion) -> dict[str, Any]:
    choice = completion.choices[0] if completion.choices else None
    return {
        "content": (choice.message.content if choice else None),
        "message": _serialize_message(choice.message) if choice else None,
        "finish_reason": getattr(choice, "finish_reason", None) if choice else None,
        "raw_usage": completion.usage.model_dump() if completion.usage else None,
    }


async def call_openai_chat(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
    max_tokens: int | None = None,
    purpose: str | None = None,
) -> ChatCompletion:
    """Single non-streaming chat completion. Logged to `llm_calls`."""
    client = get_client()

    request: dict[str, Any] = {"model": model, "messages": messages}
    if tools is not None:
        request["tools"] = tools
    if response_format is not None:
        request["response_format"] = response_format
    if max_tokens is not None:
        request["max_tokens"] = max_tokens

    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if tools is not None:
        kwargs["tools"] = tools
    if response_format is not None:
        kwargs["response_format"] = response_format
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    started_wall = datetime.now(timezone.utc)
    started_mono = time.perf_counter()

    try:
        completion = await client.chat.completions.create(**kwargs)
    except Exception as exc:
        ended_wall = datetime.now(timezone.utc)
        latency_ms = int((time.perf_counter() - started_mono) * 1000)
        fire_and_forget_write(
            started_at=started_wall,
            ended_at=ended_wall,
            latency_ms=latency_ms,
            model=model,
            request=request,
            response=None,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            streamed=False,
            purpose=purpose,
        )
        raise

    ended_wall = datetime.now(timezone.utc)
    latency_ms = int((time.perf_counter() - started_mono) * 1000)

    usage = completion.usage
    fire_and_forget_write(
        started_at=started_wall,
        ended_at=ended_wall,
        latency_ms=latency_ms,
        model=model,
        request=request,
        response=_serialize_response(completion),
        status="ok",
        streamed=False,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
        total_tokens=usage.total_tokens if usage else None,
        purpose=purpose,
    )

    return completion
