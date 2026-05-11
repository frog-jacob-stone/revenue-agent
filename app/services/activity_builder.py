"""Server-side mirror of `ChatWindow.tsx::onEvent` activity-tree building.

The chat UI builds an `ActivityLine[]` tree (tool → workflow → node → subagent
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

_NODE_LABELS: dict[str, str] = {
    # content_creation
    "content_creation:interpret_brief": "Interpreting brief",
    "content_creation:draft_post": "Drafting post",
    "content_creation:voice_review": "Reviewing voice",
    "content_creation:failed_terminal": "Voice attempts exhausted",
    # content_publish
    "content_publish:propose_post": "Proposing post",
    "content_publish:post_to_linkedin": "Posting to LinkedIn",
    # outreach_chain
    "outreach_chain:pull_hubspot": "Pulling HubSpot contact",
    "outreach_chain:web_search": "Searching the web",
    "outreach_chain:consolidate": "Consolidating research",
    "outreach_chain:retrieve_kb": "Retrieving knowledge base",
    "outreach_chain:compose_email": "Composing email",
    "outreach_chain:voice_critique": "Voice critique",
    "outreach_chain:accuracy_critique": "Accuracy critique",
    "outreach_chain:propose_send": "Proposing send",
    "outreach_chain:gmail_send": "Sending via Gmail",
    "outreach_chain:failed_terminal": "Critique attempts exhausted",
    # rev_rec_monthly
    "rev_rec_monthly:validate_and_sync": "Validating and syncing",
    "rev_rec_monthly:propose_configure": "Proposing configuration",
    "rev_rec_monthly:apply_configure_or_loop": "Applying configuration",
    "rev_rec_monthly:compute_entries": "Computing entries",
    "rev_rec_monthly:propose_write_entries": "Proposing entries",
    "rev_rec_monthly:write_entries": "Writing entries",
}

_WORKFLOW_LABELS: dict[str, str] = {
    "content_creation": "Content creation",
    "content_publish": "Publish post",
    "outreach_chain": "Outreach",
    "rev_rec_monthly": "Revenue recognition",
}


def _title_case(node: str) -> str:
    return " ".join(part.capitalize() for part in node.replace("_", " ").split())


def label_for_node(kind: str, node: str) -> str:
    return _NODE_LABELS.get(f"{kind}:{node}", _title_case(node))


def label_for_kind(kind: str) -> str:
    return _WORKFLOW_LABELS.get(kind, _title_case(kind))


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
    """Mutable cursor used by `apply_event` to thread parent-IDs across events.

    The frontend keeps these as closure variables (`toolLineId`,
    `workflowLineId`, `currentNodeLineId`, `pendingAgentByLineId`,
    `pendingAgentSlug`, `workflowKind`). Here they live on a small dataclass-
    style object so the pure function can read/write them.
    """

    __slots__ = (
        "tool_line_id",
        "workflow_line_id",
        "workflow_kind",
        "current_node_line_id",
        "pending_agent_line_id",
        "pending_agent_slug",
    )

    def __init__(self) -> None:
        self.tool_line_id: str | None = None
        self.workflow_line_id: str | None = None
        self.workflow_kind: str = ""
        self.current_node_line_id: str | None = None
        self.pending_agent_line_id: str | None = None
        self.pending_agent_slug: str | None = None


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
        state.tool_line_id = _new_id("tl")
        _push(activity, {
            "id": state.tool_line_id,
            "kind": "tool",
            "parentId": None,
            "label": f"Calling {event.get('name', '?')}",
            "status": "running",
        })
        return activity

    if etype == "workflow_started":
        workflow_id = event.get("workflow_id") or ""
        kind = event.get("kind") or ""
        state.workflow_line_id = f"wf-{workflow_id}"
        state.workflow_kind = kind
        _push(activity, {
            "id": state.workflow_line_id,
            "kind": "workflow",
            "parentId": state.tool_line_id,
            "label": f"Workflow: {label_for_kind(kind)}",
            "status": "running",
        })
        return activity

    if etype == "workflow_event":
        et = event.get("event_type")
        payload = event.get("payload") or {}

        if et == "node.entered":
            node = payload.get("node") or "?"
            state.current_node_line_id = _new_id("nd")
            _push(activity, {
                "id": state.current_node_line_id,
                "kind": "node",
                "parentId": state.workflow_line_id,
                "label": label_for_node(state.workflow_kind, node),
                "status": "running",
            })
        elif et == "node.exited":
            node = payload.get("node") or "?"
            state.current_node_line_id = _new_id("nd")
            _push(activity, {
                "id": state.current_node_line_id,
                "kind": "node",
                "parentId": state.workflow_line_id,
                "label": label_for_node(state.workflow_kind, node),
                "status": "ok",
            })
        elif et == "node.failed":
            node = payload.get("node") or "?"
            state.current_node_line_id = _new_id("nd")
            line: dict[str, Any] = {
                "id": state.current_node_line_id,
                "kind": "node",
                "parentId": state.workflow_line_id,
                "label": label_for_node(state.workflow_kind, node),
                "status": "fail",
            }
            error = payload.get("error")
            if isinstance(error, str):
                line["detail"] = error
            _push(activity, line)
        elif et == "agent.invoked":
            slug = payload.get("agent_slug") or event.get("actor") or "agent"
            state.pending_agent_slug = slug
            state.pending_agent_line_id = _new_id("ag")
            _push(activity, {
                "id": state.pending_agent_line_id,
                "kind": "subagent",
                "parentId": state.current_node_line_id,
                "label": slug,
                "status": "running",
            })
        elif et in ("agent.completed", "agent.failed"):
            tokens = _compact_tokens(payload)
            status = "ok" if et == "agent.completed" else "fail"
            if state.pending_agent_line_id:
                patch: dict[str, Any] = {
                    "status": status,
                    "label": state.pending_agent_slug or "agent",
                }
                if tokens:
                    patch["detail"] = tokens
                _patch(activity, state.pending_agent_line_id, patch)
            else:
                slug = payload.get("agent_slug") or event.get("actor") or "agent"
                line = {
                    "id": _new_id("ag"),
                    "kind": "subagent",
                    "parentId": state.current_node_line_id,
                    "label": slug,
                    "status": status,
                }
                if tokens:
                    line["detail"] = tokens
                _push(activity, line)
            state.pending_agent_line_id = None
            state.pending_agent_slug = None
        elif et == "workflow.paused":
            _push(activity, {
                "id": _new_id("pa"),
                "kind": "pause",
                "parentId": state.workflow_line_id,
                "label": "Awaiting approval",
                "status": "ok",
            })
        elif et == "workflow.completed":
            if state.workflow_line_id:
                _patch(activity, state.workflow_line_id, {"status": "ok"})
        elif et == "workflow.failed":
            if state.workflow_line_id:
                patch = {"status": "fail"}
                error = payload.get("error")
                if isinstance(error, str):
                    patch["detail"] = error
                _patch(activity, state.workflow_line_id, patch)
        return activity

    if etype == "tool_call_completed":
        if state.tool_line_id:
            patch = {"status": "ok" if event.get("ok") else "fail"}
            summary = event.get("result_summary")
            if isinstance(summary, str):
                patch["detail"] = summary
            _patch(activity, state.tool_line_id, patch)
        state.tool_line_id = None
        state.workflow_line_id = None
        state.current_node_line_id = None
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
