"""Multi-agent demo graph — illustrative reference for supervisor/specialist
patterns.

Standalone demo. Three nodes show the supervisor → specialist → review
pattern, and every agent-to-agent turn is recorded in `agent_messages` linked
to the workflow_id. Not registered in production startup; tests and smoke
scripts register it explicitly.

Topology:

    [entry] → supervisor_decide → specialist_propose → supervisor_review
                                                                │
                                                        (route_after_review)
                                                                │
                                                       ┌────────┼────────┐
                                                    approve            reject
                                                        │                │
                                                        ▼                ▼
                                                       END              END

Both branches terminate at END; the conditional exists to demonstrate the
routing pattern. The demo's value is the recorded `agent_messages` trail —
inspectable via `agent_messages.get_messages_for_workflow(workflow_id)`.
"""
from __future__ import annotations

import logging
from typing import Any, NotRequired
from uuid import UUID, uuid4

from langgraph.graph import END, StateGraph

from app.db import get_pool
from app.lib.json_utils import parse_json
from app.orchestrator.agent_invoke import NodeContext, invoke_agent
from app.orchestrator.runner import GraphSpec
from app.orchestrator.state import BaseGraphState
from app.services import agent_messages

logger = logging.getLogger(__name__)


MULTI_AGENT_DEMO_KIND = "_multi_agent_demo"

SUPERVISOR_SLUG = "outreach-agent"          # any AGENTS-registered Anthropic agent
SPECIALIST_OPTIONS = ("voice-critic", "accuracy-critic")
DEFAULT_SPECIALIST = SPECIALIST_OPTIONS[0]


# ── State ────────────────────────────────────────────────────────────────────


class MultiAgentDemoState(BaseGraphState, total=False):
    user_question: NotRequired[str]
    chosen_specialist_slug: NotRequired[str]
    proposal_question: NotRequired[str]
    proposal: NotRequired[str]
    review: NotRequired[dict[str, Any]]
    thread_id: NotRequired[str]
    result: NotRequired[dict[str, Any]]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _ctx_from_state(state: MultiAgentDemoState) -> NodeContext | None:
    wf_id = state.get("workflow_id")
    return NodeContext(workflow_id=UUID(wf_id)) if wf_id else None


def _wf_uuid(state: MultiAgentDemoState) -> UUID | None:
    wf_id = state.get("workflow_id")
    return UUID(wf_id) if wf_id else None


# ── Nodes ────────────────────────────────────────────────────────────────────


async def supervisor_decide(state: MultiAgentDemoState) -> MultiAgentDemoState:
    """Supervisor picks a specialist and frames a question for them."""
    user_question = state.get("user_question") or "Help me with an outreach email."
    thread_uuid = uuid4()
    pool = await get_pool()
    wf_id = _wf_uuid(state)

    supervisor_prompt = (
        "You are a supervisor agent coordinating two specialists:\n"
        f"  - {SPECIALIST_OPTIONS[0]} (voice/style review)\n"
        f"  - {SPECIALIST_OPTIONS[1]} (factual accuracy review)\n\n"
        f"User question: {user_question}\n\n"
        "Pick one specialist and frame a single concise question to ask them. "
        'Reply with JSON only: {"specialist_slug": "...", "question": "..."}'
    )

    # Record the supervisor "thinking out loud" as an outgoing message to itself.
    await agent_messages.send_message(
        pool,
        from_agent_slug="user",
        to_agent_slug=SUPERVISOR_SLUG,
        content=user_question,
        thread_id=thread_uuid,
        workflow_id=wf_id,
    )

    response = await invoke_agent(
        SUPERVISOR_SLUG,
        {"prompt": supervisor_prompt, "max_tokens": 300},
        _ctx_from_state(state),
    )

    parsed = parse_json(response["text"])
    chosen = str(parsed.get("specialist_slug", "")).strip() or DEFAULT_SPECIALIST
    if chosen not in SPECIALIST_OPTIONS:
        chosen = DEFAULT_SPECIALIST
    question = str(parsed.get("question", "")).strip() or user_question

    # Record the supervisor's decision as a message to the chosen specialist.
    await agent_messages.send_message(
        pool,
        from_agent_slug=SUPERVISOR_SLUG,
        to_agent_slug=chosen,
        content=question,
        thread_id=thread_uuid,
        workflow_id=wf_id,
    )

    return {
        "chosen_specialist_slug": chosen,
        "proposal_question": question,
        "thread_id": str(thread_uuid),
    }


async def specialist_propose(state: MultiAgentDemoState) -> MultiAgentDemoState:
    """Specialist responds with a proposal."""
    specialist = state.get("chosen_specialist_slug") or DEFAULT_SPECIALIST
    question = state.get("proposal_question") or ""
    thread_uuid = UUID(state["thread_id"])
    pool = await get_pool()
    wf_id = _wf_uuid(state)

    response = await invoke_agent(
        specialist,
        {"prompt": question, "max_tokens": 500},
        _ctx_from_state(state),
    )
    proposal = response["text"].strip()

    await agent_messages.send_message(
        pool,
        from_agent_slug=specialist,
        to_agent_slug=SUPERVISOR_SLUG,
        content=proposal,
        thread_id=thread_uuid,
        workflow_id=wf_id,
    )

    return {"proposal": proposal}


async def supervisor_review(state: MultiAgentDemoState) -> MultiAgentDemoState:
    """Supervisor reviews the specialist's proposal and decides approve/reject."""
    proposal = state.get("proposal") or ""
    thread_uuid = UUID(state["thread_id"])
    pool = await get_pool()
    wf_id = _wf_uuid(state)

    review_prompt = (
        "Review the following proposal from a specialist. "
        'Reply with JSON only: {"decision": "approve"|"reject", "reasoning": "..."}\n\n'
        f"PROPOSAL:\n{proposal}"
    )

    # Record the review request (supervisor → supervisor self-talk; OK for the demo).
    await agent_messages.send_message(
        pool,
        from_agent_slug=SUPERVISOR_SLUG,
        to_agent_slug=SUPERVISOR_SLUG,
        content=review_prompt,
        thread_id=thread_uuid,
        workflow_id=wf_id,
    )

    response = await invoke_agent(
        SUPERVISOR_SLUG,
        {"prompt": review_prompt, "max_tokens": 300},
        _ctx_from_state(state),
    )
    parsed = parse_json(response["text"])
    decision = str(parsed.get("decision", "approve")).strip().lower()
    if decision not in ("approve", "reject"):
        decision = "approve"
    reasoning = str(parsed.get("reasoning", "")).strip()
    review = {"decision": decision, "reasoning": reasoning}

    await agent_messages.send_message(
        pool,
        from_agent_slug=SUPERVISOR_SLUG,
        to_agent_slug=SUPERVISOR_SLUG,
        content=response["text"],
        thread_id=thread_uuid,
        workflow_id=wf_id,
    )

    return {
        "review": review,
        "result": {
            "decision": decision,
            "specialist": state.get("chosen_specialist_slug"),
            "proposal_chars": len(proposal),
        },
    }


# ── Routing ──────────────────────────────────────────────────────────────────


def route_after_review(state: MultiAgentDemoState) -> str:
    """Both decisions route to END; the branch exists to demonstrate the
    conditional-edge pattern. Future demos can split into distinct sinks
    if we want different audit signatures per outcome."""
    review = state.get("review") or {}
    if review.get("decision") == "approve":
        return "approved"
    return "rejected"


# ── Graph factory ────────────────────────────────────────────────────────────


def build_graph() -> GraphSpec:
    g: StateGraph = StateGraph(MultiAgentDemoState)

    g.add_node("supervisor_decide", supervisor_decide)
    g.add_node("specialist_propose", specialist_propose)
    g.add_node("supervisor_review", supervisor_review)

    g.set_entry_point("supervisor_decide")
    g.add_edge("supervisor_decide", "specialist_propose")
    g.add_edge("specialist_propose", "supervisor_review")
    g.add_conditional_edges(
        "supervisor_review",
        route_after_review,
        {"approved": END, "rejected": END},
    )

    return GraphSpec(graph=g)
