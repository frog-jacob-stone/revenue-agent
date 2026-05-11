"""Detached chat-turn runtime.

The chat router persists the user message + a streaming placeholder assistant
message, then hands the turn to `detach_turn` and returns immediately with an
SSE response subscribed to the runtime's live event queue.

Critically, the runtime runs in a detached asyncio task that survives the
request being cancelled — so when the user navigates away mid-stream, the
OpenAI loop keeps going, the activity tree keeps building, and the final
state is persisted to `chat_messages`. The user comes back, fetches messages,
and sees the completed turn.

There is no live re-attach for second clients: at most one SSE subscriber per
turn. If the subscriber drops, the runtime keeps running but no longer pushes
events anywhere (the activity tree stays in memory until persisted on done).
A second tab on the same chat sees `status='streaming'` and polls until done.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.services.activity_builder import ActivityState, apply_event
from app.services.chat import agent_chat_stream
from app.services.chat_sessions import finalize_assistant_message

logger = logging.getLogger(__name__)


# Module-level registry — keeps a reference to running task objects so they
# aren't garbage-collected (per asyncio.create_task docs).
_ACTIVE_TURNS: dict[UUID, "TurnRuntime"] = {}


class TurnRuntime:
    """Owns one chat turn end to end.

    Created and registered by `detach_turn`. Holds:
      - a single optional subscriber queue (the originating request's SSE)
      - the running activity tree + cursor state
      - the running content buffer (final assistant text)
      - the asyncio task driving the OpenAI loop

    On completion, writes the final state to chat_messages and removes itself
    from `_ACTIVE_TURNS` via its `done_callback`.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        session_id: UUID,
        turn_id: UUID,
        agent_slug: str,
        history: list[dict[str, str]],
    ) -> None:
        self.pool = pool
        self.session_id = session_id
        self.turn_id = turn_id
        self.agent_slug = agent_slug
        self._history = history

        self._activity: list[dict[str, Any]] = []
        self._activity_state = ActivityState()
        self._content_parts: list[str] = []
        self._tool_used: str | None = None
        self._subscriber: asyncio.Queue[dict[str, Any] | None] | None = None
        self._done = False

        self.task: asyncio.Task | None = None

    def subscribe(self) -> asyncio.Queue[dict[str, Any] | None] | None:
        """Attach a single live subscriber. Returns the queue (None sentinel
        signals end of stream). If the turn has already completed, returns None
        — caller should fetch the persisted chat_messages row instead.
        """
        if self._done:
            return None
        if self._subscriber is not None:
            # Drop the prior subscriber — at most one at a time. The old client
            # can fall back to polling chat_messages.
            try:
                self._subscriber.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._subscriber = asyncio.Queue()
        return self._subscriber

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        if self._subscriber is queue:
            self._subscriber = None

    def _emit_to_subscriber(self, event: dict[str, Any]) -> None:
        sub = self._subscriber
        if sub is None:
            return
        try:
            sub.put_nowait(event)
        except asyncio.QueueFull:
            # Should not happen with an unbounded Queue, but be defensive.
            logger.warning("chat turn subscriber queue full; dropping event")

    def _process_event(self, event: dict[str, Any]) -> None:
        """Update in-memory state for one event, then push to the subscriber."""
        etype = event.get("type")
        if etype == "delta":
            text = event.get("text")
            if isinstance(text, str):
                self._content_parts.append(text)
        elif etype == "tool_call_started":
            name = event.get("name")
            if isinstance(name, str):
                self._tool_used = name
        elif etype == "done":
            answer = event.get("answer")
            if isinstance(answer, str) and answer and not self._content_parts:
                # Belt and suspenders: if no deltas accumulated, use final answer.
                self._content_parts.append(answer)

        apply_event(self._activity, self._activity_state, event)
        self._emit_to_subscriber(event)

    async def run(self) -> None:
        """Drive the OpenAI tool-call loop and persist the final state.

        Cancellation safety: this task is created via asyncio.create_task at
        the top level, so it is NOT part of the request's cancellation scope.
        The request handler can return / disconnect / be cancelled without
        cancelling us.
        """
        terminal_status = "complete"
        error_message: str | None = None
        try:
            async for event in agent_chat_stream(self.agent_slug, self._history):
                self._process_event(event)
                if event.get("type") == "error":
                    terminal_status = "failed"
                    error_message = (
                        event.get("message") if isinstance(event.get("message"), str) else "error"
                    )
        except asyncio.CancelledError:
            terminal_status = "failed"
            error_message = "task cancelled"
            raise
        except Exception as exc:
            logger.exception("Chat turn %s failed", self.turn_id)
            terminal_status = "failed"
            error_message = f"{type(exc).__name__}: {exc}"
            # Surface to any subscriber so the live client sees the error.
            self._emit_to_subscriber({"type": "error", "message": str(exc)})
        finally:
            self._done = True
            try:
                await finalize_assistant_message(
                    self.pool,
                    turn_id=self.turn_id,
                    content="".join(self._content_parts),
                    activity=self._activity,
                    status=terminal_status,
                    tool_used=self._tool_used,
                    error=error_message,
                )
            except Exception:
                logger.exception("Failed to finalize chat message for turn %s", self.turn_id)
            # Signal end-of-stream to any subscriber.
            sub = self._subscriber
            if sub is not None:
                try:
                    sub.put_nowait(None)
                except asyncio.QueueFull:
                    pass


def detach_turn(
    *,
    pool: asyncpg.Pool,
    session_id: UUID,
    turn_id: UUID,
    agent_slug: str,
    history: list[dict[str, str]],
) -> TurnRuntime:
    """Spawn a detached background task for one chat turn. Returns the
    `TurnRuntime` so the caller can immediately `.subscribe()` for SSE."""
    runtime = TurnRuntime(
        pool=pool,
        session_id=session_id,
        turn_id=turn_id,
        agent_slug=agent_slug,
        history=history,
    )
    _ACTIVE_TURNS[turn_id] = runtime

    task = asyncio.create_task(runtime.run(), name=f"chat-turn-{turn_id}")
    runtime.task = task

    def _cleanup(_t: asyncio.Task) -> None:
        _ACTIVE_TURNS.pop(turn_id, None)

    task.add_done_callback(_cleanup)
    return runtime


def get_active(turn_id: UUID) -> TurnRuntime | None:
    return _ACTIVE_TURNS.get(turn_id)
