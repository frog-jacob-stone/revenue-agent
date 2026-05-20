"""Unit tests for app.orchestrator.critique_loop.

Builds small in-test graphs with stub draft + stub critic callables;
compiles directly with MemorySaver so the helper's loop, routing, and
state-bookkeeping behavior is isolated from the runner and the DB.
"""
from __future__ import annotations

from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.orchestrator.critique_loop import (
    Critic,
    add_critique_loop,
)


class _State(TypedDict, total=False):
    # Bookkeeping written by the stub draft node.
    drafts: list[str]
    published: bool

    # Shared slots written by the helper.
    last_critique_feedback: dict[str, Any] | None
    failure_reason: str
    result: dict[str, Any]

    # Per-critic counters/slots the helper reads/writes.
    voice_attempts: int
    voice_max_attempts: int
    last_voice_critique: dict[str, Any]
    accuracy_attempts: int
    accuracy_max_attempts: int
    last_accuracy_critique: dict[str, Any]

    # Test-stub controls: a critic fails on attempt N iff N <= {name}_fail_until.
    voice_fail_until: int
    accuracy_fail_until: int


async def _draft(state: _State) -> dict[str, Any]:
    """Stub draft: appends a draft entry, clears the shared feedback slot."""
    drafts = list(state.get("drafts", []))
    drafts.append(f"draft v{len(drafts) + 1}")
    return {"drafts": drafts, "last_critique_feedback": None}


def _make_run(critic_name: str):
    """Build a critic.run that fails while the upcoming attempt index is
    <= state[f'{name}_fail_until']. The helper hasn't incremented the
    counter yet when run() is called, so the upcoming attempt is
    state[counter] + 1.
    """
    async def run(state: _State) -> dict[str, Any]:
        upcoming = state.get(f"{critic_name}_attempts", 0) + 1
        fail_until = state.get(f"{critic_name}_fail_until", 0)
        passed = upcoming > fail_until
        return {
            "passed": passed,
            "feedback": f"{critic_name} {'ok' if passed else 'rejected'} attempt {upcoming}",
            "issues": [] if passed else [f"{critic_name} issue #{upcoming}"],
        }
    return run


async def _published_node(state: _State) -> dict[str, Any]:
    return {"published": True}


def _build_single_critic_graph(*, voice_max: int = 3):
    g: StateGraph = StateGraph(_State)
    g.add_node("draft", _draft)
    g.add_node("publish_node", _published_node)
    g.set_entry_point("draft")
    add_critique_loop(
        g,
        draft_node="draft",
        critics=[Critic("voice", _make_run("voice"), voice_max)],
        pass_target="publish_node",
    )
    g.add_edge("publish_node", END)
    return g.compile(checkpointer=MemorySaver())


def _build_multi_critic_graph(*, voice_max: int = 3, accuracy_max: int = 2):
    g: StateGraph = StateGraph(_State)
    g.add_node("draft", _draft)
    g.add_node("publish_node", _published_node)
    g.set_entry_point("draft")
    add_critique_loop(
        g,
        draft_node="draft",
        critics=[
            Critic("voice", _make_run("voice"), voice_max),
            Critic("accuracy", _make_run("accuracy"), accuracy_max),
        ],
        pass_target="publish_node",
    )
    g.add_edge("publish_node", END)
    return g.compile(checkpointer=MemorySaver())


# ── Tests ────────────────────────────────────────────────────────────────────


async def test_single_critic_passes_first_try():
    graph = _build_single_critic_graph()
    final = await graph.ainvoke(
        {"voice_fail_until": 0},
        config={"configurable": {"thread_id": "single-pass"}},
    )
    assert final.get("published") is True
    assert final["voice_attempts"] == 1
    assert not final.get("failure_reason")
    assert not final.get("result")
    assert not final.get("last_critique_feedback")


async def test_single_critic_exhausts_budget():
    graph = _build_single_critic_graph(voice_max=2)
    final = await graph.ainvoke(
        {"voice_fail_until": 99},
        config={"configurable": {"thread_id": "single-exhaust"}},
    )
    assert not final.get("published")
    assert final["voice_attempts"] == 2
    assert final["failure_reason"] == "voice budget exhausted"
    assert final["result"]["outcome"] == "failed"
    assert final["result"]["reason"] == "voice budget exhausted"
    assert final["result"]["last_critique"]["passed"] is False
    assert final["last_voice_critique"]["passed"] is False


async def test_multi_critic_both_pass_first_try():
    graph = _build_multi_critic_graph()
    final = await graph.ainvoke(
        {"voice_fail_until": 0, "accuracy_fail_until": 0},
        config={"configurable": {"thread_id": "multi-pass"}},
    )
    assert final.get("published") is True
    assert final["voice_attempts"] == 1
    assert final["accuracy_attempts"] == 1


async def test_cross_critic_counter_accumulation():
    """Voice passes attempt 1; accuracy fails attempt 1 (budget remaining);
    loop → draft → voice runs again (counter ticks to 2); accuracy passes
    attempt 2. The voice counter must NOT reset."""
    graph = _build_multi_critic_graph(voice_max=5, accuracy_max=3)
    final = await graph.ainvoke(
        # Voice never fails; accuracy fails attempt 1, passes attempt 2.
        {"voice_fail_until": 0, "accuracy_fail_until": 1},
        config={"configurable": {"thread_id": "cross-counter"}},
    )
    assert final.get("published") is True
    assert final["voice_attempts"] == 2  # ran on draft v1 and draft v2
    assert final["accuracy_attempts"] == 2


async def test_shared_slot_cleared_by_draft():
    """After a fail, last_critique_feedback is populated. The draft node
    clears it on consumption. On the subsequent pass, the helper does not
    repopulate, so the final slot is None."""
    graph = _build_single_critic_graph(voice_max=3)
    final = await graph.ainvoke(
        # Voice fails attempt 1, passes attempt 2.
        {"voice_fail_until": 1},
        config={"configurable": {"thread_id": "slot-clear"}},
    )
    assert final.get("published") is True
    assert final["voice_attempts"] == 2
    assert final.get("last_critique_feedback") is None


def test_empty_critics_raises():
    g: StateGraph = StateGraph(_State)
    g.add_node("draft", _draft)
    g.add_node("publish_node", _published_node)
    with pytest.raises(ValueError, match="at least one critic"):
        add_critique_loop(
            g,
            draft_node="draft",
            critics=[],
            pass_target="publish_node",
        )


async def test_chained_loops_share_failed_terminal():
    """Two add_critique_loop calls on the same graph. The second must reuse
    the first's failed_terminal (no double-registration error). End-to-end
    routing through both phases works for happy and exhaustion cases."""
    async def revise_for_voice(state: _State) -> dict[str, Any]:
        drafts = list(state.get("drafts", []))
        drafts.append(f"revise v{len(drafts) + 1}")
        return {"drafts": drafts, "last_critique_feedback": None}

    g: StateGraph = StateGraph(_State)
    g.add_node("initial_draft", _draft)
    g.add_node("revise_for_voice", revise_for_voice)
    g.add_node("publish_node", _published_node)
    g.set_entry_point("initial_draft")

    add_critique_loop(
        g,
        draft_node="initial_draft",
        critics=[Critic("accuracy", _make_run("accuracy"), 3)],
        pass_target="revise_for_voice",
    )
    # Must not raise on duplicate failed_terminal.
    add_critique_loop(
        g,
        draft_node="revise_for_voice",
        critics=[Critic("voice", _make_run("voice"), 3)],
        pass_target="publish_node",
    )
    g.add_edge("publish_node", END)
    graph = g.compile(checkpointer=MemorySaver())

    # Happy: both phases pass.
    final = await graph.ainvoke(
        {"accuracy_fail_until": 0, "voice_fail_until": 0},
        config={"configurable": {"thread_id": "chain-happy"}},
    )
    assert final.get("published") is True
    assert final["accuracy_attempts"] == 1
    assert final["voice_attempts"] == 1

    # Phase 1 exhausts → shared failed_terminal.
    final = await graph.ainvoke(
        {"accuracy_fail_until": 99, "accuracy_max_attempts": 2},
        config={"configurable": {"thread_id": "chain-fail-phase1"}},
    )
    assert not final.get("published")
    assert final["failure_reason"] == "accuracy budget exhausted"
    assert final["result"]["outcome"] == "failed"

    # Phase 1 passes; Phase 2 exhausts → same failed_terminal.
    final = await graph.ainvoke(
        {
            "accuracy_fail_until": 0,
            "voice_fail_until": 99,
            "voice_max_attempts": 2,
        },
        config={"configurable": {"thread_id": "chain-fail-phase2"}},
    )
    assert not final.get("published")
    assert final["failure_reason"] == "voice budget exhausted"
    assert final["result"]["outcome"] == "failed"
