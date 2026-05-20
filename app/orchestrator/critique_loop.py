"""Critique-loop builder.

Lifts the draft → critic → (loop or terminate) policy out of individual
graphs into a single deep module. Hosts declare their critics; the helper
owns counters, budgets, the shared feedback slot, edge routing, and the
`failed_terminal` node.

Two topologies are supported via composition:

1. **Multi-critic-per-loop** — pass a list of critics in one call. Every
   redraft re-runs all critics in order. Counters accumulate monotonically
   across the workflow (critic A's counter keeps its value when critic B
   triggers the loop).

       add_critique_loop(
           g,
           draft_node="compose_email",
           critics=[
               Critic("voice", run_voice, 3),
               Critic("accuracy", run_accuracy, 2),
           ],
           pass_target="propose_send",
       )

2. **Phased validation** — chain two calls. The first critic fully
   resolves before the second begins. Each phase has its own draft/revise
   node. Both calls share the same `failed_terminal` (the first registers
   it, the second reuses it).

       add_critique_loop(
           g,
           draft_node="initial_draft",
           critics=[Critic("accuracy", run_accuracy, 3)],
           pass_target="revise_for_voice",
       )
       add_critique_loop(
           g,
           draft_node="revise_for_voice",
           critics=[Critic("voice", run_voice, 3)],
           pass_target="propose_send",
       )

State-key convention. Hosts must declare these fields in their TypedDict
by critic name:

- `{name}_attempts`         — monotonic counter (helper writes).
- `{name}_max_attempts`     — optional per-workflow override; falls back to
                              `Critic.max_attempts`.
- `last_{name}_critique`    — full last critique dict (for inspection).
- `last_critique_feedback`  — shared slot, set on any fail. **The draft
                              node must clear it after consumption** (set
                              it to `None` in the draft's return dict).
- `failure_reason`          — set on the exhausting attempt by the helper;
                              read by `failed_terminal`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypedDict

from langgraph.graph import END, StateGraph


FAILED_TERMINAL_NODE = "failed_terminal"


class CritiqueResult(TypedDict, total=False):
    """What a critic's `run` callable returns.

    `passed` is required at runtime. `feedback` and `issues` are the
    conventional fields the draft node surfaces back to the model on a
    retry. Extra keys (e.g. `revised_post_text`, `score`,
    `suggested_changes`) are preserved in `last_{name}_critique`.
    """
    passed: bool
    feedback: str
    issues: list[str]


@dataclass
class Critic:
    """One critic in a critique loop.

    Attributes:
        name: drives the node name (`f"{name}_critique"`) and the state
            keys (`{name}_attempts`, `last_{name}_critique`).
        run: async callable taking the graph state and returning a
            CritiqueResult-shaped dict. Host writes the LLM call, parse,
            and any side effects.
        max_attempts: default budget; can be overridden per-workflow by
            setting `{name}_max_attempts` in the initial state.
    """
    name: str
    run: Callable[[Any], Awaitable[dict[str, Any]]]
    max_attempts: int


async def _default_failed_terminal(state: dict[str, Any]) -> dict[str, Any]:
    """Standard terminal body. Reads `failure_reason` (set by the wrapped
    critic on its exhausting attempt) and the shared last-critique slot."""
    return {
        "result": {
            "outcome": "failed",
            "reason": state.get("failure_reason") or "critique budget exhausted",
            "last_critique": state.get("last_critique_feedback"),
        },
    }


def _make_critic_node(
    critic: Critic,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Wrap a host-provided `critic.run` with counter, shared-slot, and
    failure-reason bookkeeping."""
    name = critic.name

    async def critic_node(state: dict[str, Any]) -> dict[str, Any]:
        critique = await critic.run(state)
        attempts = state.get(f"{name}_attempts", 0) + 1
        max_attempts = state.get(f"{name}_max_attempts", critic.max_attempts)

        update: dict[str, Any] = {
            f"{name}_attempts": attempts,
            f"last_{name}_critique": critique,
        }
        if not critique.get("passed"):
            update["last_critique_feedback"] = critique
            if attempts >= max_attempts:
                update["failure_reason"] = f"{name} budget exhausted"
        return update

    critic_node.__name__ = f"{name}_critique"
    return critic_node


def _make_router(
    critic: Critic,
    pass_target: Any,
    draft_node: str,
) -> Callable[[dict[str, Any]], Any]:
    """Build the conditional-edge router for one critic.

    pass → `pass_target` (next critic, or final pass_target if last).
    fail + budget remaining → `draft_node` (loop).
    fail + budget exhausted → `failed_terminal`.
    """
    name = critic.name
    default_max = critic.max_attempts

    def route(state: dict[str, Any]) -> Any:
        last = state.get(f"last_{name}_critique") or {}
        if last.get("passed"):
            return pass_target
        attempts = state.get(f"{name}_attempts", 0)
        max_attempts = state.get(f"{name}_max_attempts", default_max)
        if attempts >= max_attempts:
            return FAILED_TERMINAL_NODE
        return draft_node

    route.__name__ = f"route_after_{name}"
    return route


def add_critique_loop(
    g: StateGraph,
    *,
    draft_node: str,
    critics: list[Critic],
    pass_target: Any,
) -> None:
    """Attach a critique loop to `g` (mutates in place).

    Adds one node per critic (`f"{c.name}_critique"`), wires the loop
    edges, and — if a node named `failed_terminal` is not already in `g` —
    registers a shared `failed_terminal` node. Subsequent calls on the
    same graph reuse the existing terminal.

    Args:
        g: a StateGraph with `draft_node` and `pass_target` already added
            (unless `pass_target` is `langgraph.END`).
        draft_node: name of the host's draft node. The helper adds
            `draft_node → {first critic}_critique` and, on a loop, routes
            back to `draft_node`.
        critics: ordered list. Each runs after the previous passes.
        pass_target: where to route when every critic passes. May be a
            node name or `langgraph.END`.

    Raises:
        ValueError: if `critics` is empty.
    """
    if not critics:
        raise ValueError("add_critique_loop requires at least one critic")

    if FAILED_TERMINAL_NODE not in g.nodes:
        g.add_node(FAILED_TERMINAL_NODE, _default_failed_terminal)
        g.add_edge(FAILED_TERMINAL_NODE, END)

    for critic in critics:
        g.add_node(f"{critic.name}_critique", _make_critic_node(critic))

    g.add_edge(draft_node, f"{critics[0].name}_critique")

    for i, critic in enumerate(critics):
        is_last = i == len(critics) - 1
        next_on_pass: Any = pass_target if is_last else f"{critics[i + 1].name}_critique"

        router = _make_router(critic, next_on_pass, draft_node)
        node_name = f"{critic.name}_critique"

        path_map: dict[Any, Any] = {
            next_on_pass: next_on_pass,
            draft_node: draft_node,
            FAILED_TERMINAL_NODE: FAILED_TERMINAL_NODE,
        }
        g.add_conditional_edges(node_name, router, path_map)
