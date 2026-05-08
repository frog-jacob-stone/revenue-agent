"""Conventions for graph state TypedDicts used by orchestrator_v2.

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
