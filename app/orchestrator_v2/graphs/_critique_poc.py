"""Critique-loop proof-of-concept graph.

Standalone reference for the cycle pattern Phase 3 will use to migrate the
outreach and content_creation chains. Not a real workflow — no LLMs, no DB
writes beyond the runner's audit events. Three nodes:

    draft → critique → publish
                ↓ (fail with budget)
              draft (cycle)
                ↓ (budget exhausted)
              failed (terminal)

Used by tests/test_critique_loop_poc.py and intentionally deletable in
Phase 5 cleanup once outreach/content_creation are migrated.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict

from langgraph.graph import END, StateGraph

from app.orchestrator_v2.runner import GraphSpec


CRITIQUE_POC_KIND = "_critique_poc"


class CritiquePOCState(TypedDict, total=False):
    workflow_id: NotRequired[str]
    drafts: list[str]              # one entry per draft attempt
    attempts: int
    max_attempts: int
    fail_until: int                # critique fails on attempts < fail_until
    last_critique: NotRequired[dict]
    published: NotRequired[bool]
    budget_exhausted: NotRequired[bool]


def _draft_node(state: CritiquePOCState) -> CritiquePOCState:
    attempts = state.get("attempts", 0) + 1
    drafts = list(state.get("drafts", []))
    drafts.append(f"draft v{attempts}")
    return {"attempts": attempts, "drafts": drafts}


def _critique_node(state: CritiquePOCState) -> CritiquePOCState:
    attempts = state.get("attempts", 0)
    fail_until = state.get("fail_until", 0)
    passed = attempts >= fail_until
    return {"last_critique": {"passed": passed, "attempt": attempts}}


def _publish_node(state: CritiquePOCState) -> CritiquePOCState:
    return {"published": True}


def _failed_terminal_node(state: CritiquePOCState) -> CritiquePOCState:
    return {"budget_exhausted": True}


def _route_after_critique(state: CritiquePOCState) -> str:
    """Pass → publish; fail with budget → draft; fail with no budget → failed."""
    last = state.get("last_critique") or {}
    attempts = state.get("attempts", 0)
    max_attempts = state.get("max_attempts", 1)
    if last.get("passed"):
        return "publish"
    if attempts >= max_attempts:
        return "failed"
    return "draft"


def build_graph() -> GraphSpec:
    g: StateGraph = StateGraph(CritiquePOCState)
    g.add_node("draft", _draft_node)
    g.add_node("critique", _critique_node)
    g.add_node("publish", _publish_node)
    g.add_node("failed", _failed_terminal_node)

    g.set_entry_point("draft")
    g.add_edge("draft", "critique")
    g.add_conditional_edges(
        "critique",
        _route_after_critique,
        {"draft": "draft", "publish": "publish", "failed": "failed"},
    )
    g.add_edge("publish", END)
    g.add_edge("failed", END)
    return GraphSpec(graph=g)
