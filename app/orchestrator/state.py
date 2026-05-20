"""Conventions for graph state TypedDicts used by orchestrator.

Each graph defines its own TypedDict that extends BaseGraphState. State is
the graph's runtime data (drafts, retrieved KB chunks, attempt counts).
Long-lived domain state lives in dedicated tables (e.g. social_posts).

Convention: when a node wants the runner to pause for human approval, it
includes a `_propose` key in its returned state with the payload the runner
should write to the `approvals` table. The runner reads `_propose` and
strips it from the persisted state before yielding.
"""
from __future__ import annotations

from typing import Any, NotRequired, TypedDict
from uuid import UUID

from app.integrations.llm import Attribution


class ProposeApproval(TypedDict, total=False):
    """Payload a node returns under state["_propose"] to request human approval."""
    action_type: str
    agent_slug: str
    summary: str
    reasoning: str
    risk_level: str
    proposed_payload: dict[str, Any]
    assigned_to: str


class BaseGraphState(TypedDict):
    """Minimal shape every graph's state must extend."""
    workflow_id: NotRequired[str]
    parent_workflow_id: NotRequired[str | None]
    _propose: NotRequired[ProposeApproval]
    # Slug of the agent identity this workflow's work attributes to. Seeded by
    # the runner from `GraphSpec.owning_agent` (default) or the invoker's
    # override on `runner.start(...)`. Every LLM dispatch inside the workflow
    # uses this for `Attribution.agent_slug`. None when no owning agent is
    # declared and no override is supplied.
    _owning_agent_slug: NotRequired[str | None]


def make_attribution(state: BaseGraphState, purpose: str) -> Attribution:
    """Build an Attribution for a dispatch from a graph node.

    `agent_slug` comes from the workflow's owning agent (seeded by the runner
    from the GraphSpec default or an invoker override); `purpose` discriminates
    the sub-step.
    """
    wf_id = state.get("workflow_id")
    return Attribution(
        agent_slug=state.get("_owning_agent_slug"),
        purpose=purpose,
        workflow_id=UUID(wf_id) if wf_id else None,
    )
