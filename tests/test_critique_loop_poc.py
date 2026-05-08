"""Critique-loop POC tests.

Validates the LangGraph cycle pattern that Phase 3 will use for outreach and
content_creation chains. No DB, no runner — just the compiled graph driven
directly so the cycle behavior is isolated.
"""
from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.orchestrator_v2.graphs._critique_poc import build_graph


def _compile():
    spec = build_graph()
    return spec.graph.compile(checkpointer=MemorySaver())


@pytest.mark.parametrize(
    "fail_until,max_attempts,expect_published,expect_attempts",
    [
        (0, 3, True, 1),   # passes immediately
        (3, 5, True, 3),   # fails twice then passes on third
        (10, 2, False, 2), # never passes; exhausts budget at attempt 2
    ],
)
async def test_critique_cycle_terminates(
    fail_until, max_attempts, expect_published, expect_attempts,
):
    graph = _compile()
    config = {"configurable": {"thread_id": f"poc-{fail_until}-{max_attempts}"}}
    final = await graph.ainvoke(
        {"attempts": 0, "max_attempts": max_attempts, "fail_until": fail_until},
        config=config,
    )

    assert final["attempts"] == expect_attempts
    if expect_published:
        assert final.get("published") is True
        assert final.get("budget_exhausted") is not True
    else:
        assert final.get("budget_exhausted") is True
        assert final.get("published") is not True

    # Each cycle adds one draft.
    assert len(final["drafts"]) == expect_attempts
