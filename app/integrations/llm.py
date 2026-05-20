"""LLM dispatcher.

The single seam in front of every LLM call. Callers reach for `dispatch()` or
`dispatch_stream()`; provider knowledge stays behind this module. Every call
writes exactly one row to `llm_calls`, attributed by the required `Attribution`
argument — there is no contextvar form, so forgetting attribution is impossible.

Adding a second provider means writing one more `LlmProvider` adapter and
registering it as the module default. Callers do not change.
"""
from __future__ import annotations

import dataclasses
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterator, Protocol
from uuid import UUID

from app.services.llm_logging import write_llm_call

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Attribution:
    """Required on every dispatch. Lands on every `llm_calls` row.

    `purpose` is a dotted free-form label (e.g. `"outreach.compose_email"`,
    `"chat"`, `"agent:bdr"`). Used for slicing telemetry and tracing.
    """

    agent_slug: str
    purpose: str
    workflow_id: UUID | None = None
    thread_id: UUID | None = None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LlmResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class StreamDelta:
    """One increment from a streaming dispatch.

    Either text-bearing (`text != ""`) or tool-call-bearing (`tool_call_index
    is not None`). The terminal event in a stream is an `LlmResponse`, not a
    `StreamDelta`.
    """

    text: str = ""
    tool_call_index: int | None = None
    tool_call_id: str | None = None
    tool_call_name_delta: str | None = None
    tool_call_args_delta: str | None = None


class LlmProvider(Protocol):
    """Provider port. One production adapter (OpenAI), one test adapter (fake).

    Implementations live in `app/integrations/_<name>_provider.py`. Selecting
    a provider for tests is done via `use_provider()`.
    """

    name: str

    async def complete(self, request: dict[str, Any]) -> LlmResponse: ...

    def stream(
        self, request: dict[str, Any]
    ) -> AsyncIterator[StreamDelta | LlmResponse]: ...


# ── Provider resolution ─────────────────────────────────────────────────────

_provider_override: ContextVar[LlmProvider | None] = ContextVar(
    "llm_provider_override", default=None
)
_default_provider_instance: LlmProvider | None = None


def _default_provider() -> LlmProvider:
    """Lazy-import the OpenAI provider so tests can scope a fake before first use."""
    global _default_provider_instance
    if _default_provider_instance is None:
        from app.integrations._openai_provider import OpenAiProvider

        _default_provider_instance = OpenAiProvider()
    return _default_provider_instance


def _resolve_provider() -> LlmProvider:
    override = _provider_override.get()
    if override is not None:
        return override
    return _default_provider()


@contextmanager
def use_provider(provider: LlmProvider) -> Iterator[None]:
    """Scope a provider override (typically a fake) to the `with` block.

    Nested overrides nest correctly via ContextVar. The override is per-task,
    so concurrent test cases do not bleed providers across each other.
    """
    token = _provider_override.set(provider)
    try:
        yield
    finally:
        _provider_override.reset(token)


# ── Public dispatch surface ─────────────────────────────────────────────────


async def dispatch(
    *,
    model: str,
    messages: list[dict[str, Any]],
    attribution: Attribution,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
    max_tokens: int | None = None,
) -> LlmResponse:
    """Non-streaming dispatch.

    Writes exactly one `llm_calls` row (success or error). Errors are re-raised
    after the row is queued.
    """
    provider = _resolve_provider()
    request = _build_request(
        model=model,
        messages=messages,
        tools=tools,
        response_format=response_format,
        max_tokens=max_tokens,
    )

    started_wall = datetime.now(timezone.utc)
    started_mono = time.perf_counter()

    try:
        response = await provider.complete(request)
    except Exception as exc:
        await _emit_row(
            attribution=attribution,
            provider=provider.name,
            model=model,
            request=request,
            response=None,
            started_wall=started_wall,
            started_mono=started_mono,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            streamed=False,
        )
        raise

    await _emit_row(
        attribution=attribution,
        provider=provider.name,
        model=model,
        request=request,
        response=response,
        started_wall=started_wall,
        started_mono=started_mono,
        status="ok",
        error=None,
        streamed=False,
    )
    return response


async def dispatch_stream(
    *,
    model: str,
    messages: list[dict[str, Any]],
    attribution: Attribution,
    tools: list[dict[str, Any]] | None = None,
) -> AsyncIterator[StreamDelta | LlmResponse]:
    """Streaming dispatch.

    Yields zero or more `StreamDelta`, then exactly one terminal `LlmResponse`.
    Writes one `llm_calls` row on stream completion (success or error).
    Callers may `break` early; the row is still written with `status="ok"` and
    whatever was assembled up to that point.
    """
    provider = _resolve_provider()
    request = _build_request(
        model=model,
        messages=messages,
        tools=tools,
        response_format=None,
        max_tokens=None,
    )
    request["stream"] = True

    started_wall = datetime.now(timezone.utc)
    started_mono = time.perf_counter()

    final: LlmResponse | None = None
    try:
        async for evt in provider.stream(request):
            if isinstance(evt, LlmResponse):
                final = evt
            yield evt
    except Exception as exc:
        await _emit_row(
            attribution=attribution,
            provider=provider.name,
            model=model,
            request=request,
            response=final,
            started_wall=started_wall,
            started_mono=started_mono,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            streamed=True,
        )
        raise

    await _emit_row(
        attribution=attribution,
        provider=provider.name,
        model=model,
        request=request,
        response=final,
        started_wall=started_wall,
        started_mono=started_mono,
        status="ok",
        error=None,
        streamed=True,
    )


# ── Internals ───────────────────────────────────────────────────────────────


def _build_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    response_format: dict[str, Any] | None,
    max_tokens: int | None,
) -> dict[str, Any]:
    request: dict[str, Any] = {"model": model, "messages": list(messages)}
    if tools is not None:
        request["tools"] = tools
    if response_format is not None:
        request["response_format"] = response_format
    if max_tokens is not None:
        request["max_tokens"] = max_tokens
    return request


async def _emit_row(
    *,
    attribution: Attribution,
    provider: str,
    model: str,
    request: dict[str, Any],
    response: LlmResponse | None,
    started_wall: datetime,
    started_mono: float,
    status: str,
    error: str | None,
    streamed: bool,
) -> None:
    """Write the `llm_calls` row. Telemetry failures are swallowed and logged."""
    ended_wall = datetime.now(timezone.utc)
    latency_ms = int((time.perf_counter() - started_mono) * 1000)

    response_dict: dict[str, Any] | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    if response is not None:
        response_dict = dataclasses.asdict(response)
        prompt_tokens = response.prompt_tokens
        completion_tokens = response.completion_tokens
        if prompt_tokens is not None and completion_tokens is not None:
            total_tokens = prompt_tokens + completion_tokens

    await write_llm_call(
        started_at=started_wall,
        ended_at=ended_wall,
        latency_ms=latency_ms,
        model=model,
        request=request,
        response=response_dict,
        status=status,
        error=error,
        streamed=streamed,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        agent_slug=attribution.agent_slug,
        workflow_id=attribution.workflow_id,
        thread_id=attribution.thread_id,
        purpose=attribution.purpose,
        provider=provider,
    )
