"""Test fake for `LlmProvider`.

Two modes:

  - **Queued**: scripted `completions=[...]` or `streams=[...]` are popped FIFO.
  - **Router**: `respond=fn` and/or `stream=fn` take the request dict and
    return the response (or stream events). Use this when the test needs to
    branch on prompt content.

Recorded requests live on `requests` for assertions.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable

from app.integrations.llm import LlmResponse, StreamDelta

RespondFn = Callable[[dict[str, Any]], LlmResponse | Awaitable[LlmResponse]]
StreamFn = Callable[
    [dict[str, Any]],
    list[StreamDelta | LlmResponse]
    | Awaitable[list[StreamDelta | LlmResponse]],
]


class FakeProvider:
    name: str = "fake"

    def __init__(
        self,
        *,
        completions: list[LlmResponse | Exception] | None = None,
        streams: list[list[StreamDelta | LlmResponse] | Exception] | None = None,
        respond: RespondFn | None = None,
        stream: StreamFn | None = None,
    ) -> None:
        self._completions: list[LlmResponse | Exception] = list(completions or [])
        self._streams: list[list[StreamDelta | LlmResponse] | Exception] = list(streams or [])
        self._respond = respond
        self._stream = stream
        self.requests: list[dict[str, Any]] = []

    async def complete(self, request: dict[str, Any]) -> LlmResponse:
        self.requests.append(request)
        if self._respond is not None:
            result = self._respond(request)
            if isinstance(result, LlmResponse):
                return result
            return await result  # type: ignore[no-any-return]
        if not self._completions:
            raise RuntimeError("FakeProvider has no completions queued")
        nxt = self._completions.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    async def stream(
        self, request: dict[str, Any]
    ) -> AsyncIterator[StreamDelta | LlmResponse]:
        self.requests.append(request)
        events: list[StreamDelta | LlmResponse]
        if self._stream is not None:
            result = self._stream(request)
            if isinstance(result, list):
                events = result
            else:
                events = await result  # type: ignore[assignment]
        else:
            if not self._streams:
                raise RuntimeError("FakeProvider has no streams queued")
            nxt = self._streams.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            events = nxt
        for evt in events:
            yield evt
