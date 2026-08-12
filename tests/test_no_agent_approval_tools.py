"""ADR-0004 condition 2, enforced in CI rather than by comment.

Operator-initiated writes skip the approval row because a human is present and
looking at the payload. That trade is only sound while the write stays
unreachable from an LLM. The moment a write tool lands in some agent's
`allowed_tools`, that agent can act without a human present *and* without an
approval row — strictly worse than either pattern alone.

So: no tool reachable by any agent may return `AwaitingApproval`.

This scans handler source rather than calling the tools, for the same reason
`test_harvest_write_guardrail.py` scans source: the guarantee wanted is "no agent
can reach this", not "no agent reached it on the path we happened to exercise".

Consequence worth stating plainly — this test failing does not mean "add an
approval row". It means someone gave an agent a write tool, and the question to
answer is whether that write should be agent-initiated at all.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.agents.registry import AGENTS
from app.agents.tools.base import ToolDefinition

_MARKER = "AwaitingApproval"

# Every (agent_slug, tool) pair an LLM can reach. Parametrized so a failure names
# the offending pair rather than dumping a list.
_PAIRS: list[tuple[str, ToolDefinition]] = [
    (cls.slug, tool) for cls in AGENTS for tool in cls.allowed_tools
]


def _handler_source(tool: ToolDefinition) -> tuple[Path | None, str]:
    """The source of the tool's handler, or ('', '') if it cannot be resolved."""
    fn = inspect.unwrap(tool.execute)
    try:
        path = inspect.getsourcefile(fn)
        return (Path(path) if path else None, inspect.getsource(fn))
    except (OSError, TypeError):  # C-implemented or dynamically built
        return (None, "")


@pytest.mark.parametrize(
    "agent_slug,tool", _PAIRS, ids=[f"{slug}:{t.name}" for slug, t in _PAIRS]
)
def test_agent_tool_cannot_await_approval(agent_slug: str, tool: ToolDefinition) -> None:
    path, source = _handler_source(tool)

    assert source, (
        f"Could not read the source of {tool.name}'s handler, so it cannot be "
        "checked. Tools must be plain module-level async functions."
    )

    if _MARKER in source:
        pytest.fail(
            f"Agent '{agent_slug}' can call '{tool.name}', whose handler returns "
            f"{_MARKER} ({path}).\n\n"
            "Per ADR-0004 no agent may hold a tool that proposes an approval. "
            "Either remove the tool from that agent's allowed_tools, or make the "
            "write operator-initiated (human-only endpoint + payload shown before "
            "the click + audit_log)."
        )


def test_the_scan_can_actually_detect_a_violation() -> None:
    """Guard the guard.

    A source scan that silently matches nothing passes forever. `publish_post`
    was removed from `LinkedInAgent` by ADR-0004 but still exists and still
    returns `AwaitingApproval`, which makes it the honest fixture for proving
    the detection works.
    """
    from app.agents.tools.content import PUBLISH_POST

    _, source = _handler_source(PUBLISH_POST)
    assert source, "publish_post's handler source should be readable"
    assert _MARKER in source, (
        "publish_post no longer returns AwaitingApproval, so this test no longer "
        "proves the scan detects anything. Point it at another approval-proposing "
        "tool, or delete it along with the last one."
    )


def test_approval_proposing_tools_are_unreachable_but_still_registered() -> None:
    """ADR-0004 preserves the machinery; it does not delete it.

    Both executors stay registered so agentic execution can return without a
    reimplementation. If this fails, someone removed an executor — which is a
    real decision, not a cleanup.
    """
    from app.executors.registry import EXECUTORS_BY_NAME

    assert "post_to_linkedin" in EXECUTORS_BY_NAME
    assert "write_rev_rec_entries" in EXECUTORS_BY_NAME
