"""Server-side mirror of `ChatWindow.tsx::onEvent` activity-tree building.

The chat UI builds an `ActivityLine[]` tree (tool → tool-step / sub-agent
nesting) from the SSE event stream as it arrives. When the user reloads or
returns to a chat that was started while they were watching, the frontend
renders the persisted tree from `chat_messages.activity` instead of rebuilding
from events. To keep both paths consistent, the turn runtime builds the same
tree on the server and persists it on completion.

This module is the canonical server-side builder. If the frontend logic in
`ChatWindow.tsx::onEvent` ever drifts, the version here wins (it's what the
user actually sees on reload).
"""
from __future__ import annotations

import secrets
from typing import Any


# Mirror of ui/src/pages/Chat/nodeLabels.ts. Keep in sync.

# Tool-step labels (ADR-0002). Used when a tool emits `tool_step_started` /
# `tool_step_completed` events on its ProgressEmitter.
_TOOL_STEP_LABELS: dict[str, str] = {
    "create_post:interpret_brief": "Interpreting brief",
    "create_post:draft_post": "Drafting post",
    "create_post:voice_review": "Reviewing voice",
    "trigger_revenue_recognition:validate_and_sync": "Validating and syncing",
    "trigger_revenue_recognition:compute_entries": "Computing entries",
}


def _title_case(node: str) -> str:
    return " ".join(part.capitalize() for part in node.replace("_", " ").split())


def label_for_tool_step(tool: str, step: str) -> str:
    """Label for a `tool_step_*` event (ADR-0002)."""
    return _TOOL_STEP_LABELS.get(f"{tool}:{step}", _title_case(step))


def _compact_tokens(payload: dict[str, Any]) -> str | None:
    total = payload.get("total_tokens")
    if not isinstance(total, int) or total <= 0:
        usage = payload.get("usage") or {}
        if isinstance(usage, dict):
            total = usage.get("total_tokens")
    if not isinstance(total, int) or total <= 0:
        return None
    return f"{total / 1000:.1f}k tokens" if total >= 1000 else f"{total} tokens"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


class ActivityState:
    """Mutable cursor used by `apply_event` to thread parent-IDs across events."""

    __slots__ = (
        "tool_line_id",
        "tool_name",
        "agent_task_tool_line_id",
        "tool_step_line_ids",
    )

    def __init__(self) -> None:
        self.tool_line_id: str | None = None
        self.tool_name: str | None = None
        self.agent_task_tool_line_id: str | None = None
        self.tool_step_line_ids: dict[str, str] = {}


def _push(activity: list[dict[str, Any]], line: dict[str, Any]) -> None:
    activity.append(line)


def _patch(activity: list[dict[str, Any]], line_id: str, patch: dict[str, Any]) -> None:
    for line in activity:
        if line["id"] == line_id:
            line.update(patch)
            return


def apply_event(
    activity: list[dict[str, Any]],
    state: ActivityState,
    event: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply one SSE event to the activity tree. Mutates `activity` and `state`.

    Mirrors the `onEvent` switch in `ChatWindow.tsx`.
    """
    etype = event.get("type")

    if etype == "delta":
        return activity

    if etype == "tool_call_started":
        name = event.get("name") or "?"
        state.tool_line_id = _new_id("tl")
        state.tool_name = name if isinstance(name, str) else "?"
        state.tool_step_line_ids = {}
        _push(activity, {
            "id": state.tool_line_id,
            "kind": "tool",
            "parentId": None,
            "label": f"Calling {name}",
            "status": "running",
        })
        return activity

    if etype == "tool_step_started":
        step = event.get("name") or "?"
        tool = state.tool_name or ""
        line_id = _new_id("ts")
        state.tool_step_line_ids[str(step)] = line_id
        _push(activity, {
            "id": line_id,
            "kind": "node",
            "parentId": state.tool_line_id,
            "label": label_for_tool_step(tool, str(step)),
            "status": "running",
        })
        return activity

    if etype == "tool_step_completed":
        step = event.get("name") or "?"
        line_id = state.tool_step_line_ids.get(str(step))
        if line_id:
            patch: dict[str, Any] = {"status": "ok" if event.get("ok", True) else "fail"}
            detail = event.get("detail")
            if isinstance(detail, str):
                patch["detail"] = detail
            _patch(activity, line_id, patch)
        return activity

    if etype == "agent_task_tool_started":
        state.agent_task_tool_line_id = _new_id("at")
        agent = event.get("agent_slug") or "agent"
        name = event.get("name") or "?"
        _push(activity, {
            "id": state.agent_task_tool_line_id,
            "kind": "tool",
            "parentId": state.tool_line_id,
            "label": f"{agent} → {name}",
            "status": "running",
        })
        return activity

    if etype == "agent_task_tool_completed":
        if state.agent_task_tool_line_id:
            patch = {"status": "ok" if event.get("ok") else "fail"}
            summary = event.get("result_summary")
            if isinstance(summary, str):
                patch["detail"] = summary
            _patch(activity, state.agent_task_tool_line_id, patch)
        state.agent_task_tool_line_id = None
        return activity

    if etype == "tool_call_completed":
        if state.tool_line_id:
            patch = {"status": "ok" if event.get("ok") else "fail"}
            summary = event.get("result_summary")
            if isinstance(summary, str):
                patch["detail"] = summary
            _patch(activity, state.tool_line_id, patch)
        state.tool_line_id = None
        state.tool_name = None
        state.agent_task_tool_line_id = None
        state.tool_step_line_ids = {}
        return activity

    if etype == "error":
        _push(activity, {
            "id": _new_id("er"),
            "kind": "error",
            "parentId": None,
            "label": event.get("message") or "error",
            "status": "fail",
        })
        return activity

    # 'done' and unknown events are no-ops for activity.
    return activity
