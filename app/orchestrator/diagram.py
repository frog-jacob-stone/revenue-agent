"""Render registered Chains as Mermaid flowcharts and JSON structures.

Pure transforms: no I/O, no FastAPI deps. Live next to chain.py / steps.py
because they read those types and nothing else.

Mermaid rendering rules:
  - Auto steps (task, llm_step) → rectangle
  - Human-pause steps (checkpoint, execution) → parallelogram
  - Critique steps → rhombus, with explicit pass / fail (retry) / budget edges
  - Steps with skip_if → preceded by a labeled rhombus gate; consecutive steps
    sharing a skip_if_label collapse under one gate to avoid visual noise
    (e.g. rev_rec's two trailing steps both skipped on "validation failed").

Limitations:
  - skip_if predicates and on_approve callbacks are opaque Python; we only
    surface the optional `skip_if_label` / `on_approve_label` declared on the
    Step. No predicate is ever evaluated here.
  - Mutually exclusive skip conditions (e.g. "passed" vs "failed") still emit
    independent gates; the diagram is correct as a state machine but does not
    encode predicate disjointness.
"""
from __future__ import annotations

from typing import Any

from app.orchestrator.chain import Chain
from app.orchestrator.steps import (
    CheckpointStep,
    CritiqueStep,
    ExecutionStep,
    LLMStep,
    Step,
    TaskStep,
)


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def chain_to_mermaid(chain: Chain) -> str:
    """Emit a Mermaid `flowchart TD` source string for the given chain."""
    lines: list[str] = ["flowchart TD"]
    step_ids = [f"s{i}" for i in range(len(chain.steps))]
    has_critique = any(isinstance(s, CritiqueStep) for s in chain.steps)

    # Terminal nodes
    lines.append("    start([Start])")
    lines.append("    done([Done])")
    if has_critique:
        lines.append("    fail([Failed])")

    # Step nodes
    for i, step in enumerate(chain.steps):
        lines.append(f"    {step_ids[i]}{_step_node_body(step)}")

    # Skip-if gates (collapsing consecutive same-label runs)
    gate_for_step: dict[int, str | None] = {}
    last_label: str | None = None
    for i, step in enumerate(chain.steps):
        if step.skip_if is None:
            last_label = None
            continue
        label = step.skip_if_label or "may skip"
        if label == last_label:
            # Share the previous gate's skip branch — no new gate emitted.
            gate_for_step[i] = None
        else:
            gate_id = f"g{i}"
            gate_for_step[i] = gate_id
            lines.append(f'    {gate_id}{{"{_escape(label)}?"}}')
            last_label = label

    # Edge helpers
    def entry_node(i: int) -> str:
        if i >= len(chain.steps):
            return "done"
        gate = gate_for_step.get(i)
        return gate if gate is not None else step_ids[i]

    def skip_destination(i: int) -> str:
        my_label = chain.steps[i].skip_if_label or "may skip"
        j = i + 1
        while j < len(chain.steps):
            sj = chain.steps[j]
            if sj.skip_if is None:
                break
            if (sj.skip_if_label or "may skip") != my_label:
                break
            j += 1
        return entry_node(j)

    # Edges
    lines.append(f"    start --> {entry_node(0)}")
    for i, step in enumerate(chain.steps):
        gate = gate_for_step.get(i)
        if gate is not None:
            label = step.skip_if_label or "may skip"
            lines.append(f'    {gate} -- "{_escape(label)}" --> {skip_destination(i)}')
            lines.append(f'    {gate} -- "no" --> {step_ids[i]}')

        if isinstance(step, CritiqueStep):
            lines.append(f'    {step_ids[i]} -- "pass" --> {entry_node(i + 1)}')
            target = step_ids[step.critiques_step_index]
            lines.append(f'    {step_ids[i]} -- "fail (retry)" --> {target}')
            lines.append(f'    {step_ids[i]} -- "budget exhausted" --> fail')
        else:
            lines.append(f"    {step_ids[i]} --> {entry_node(i + 1)}")

    return "\n".join(lines) + "\n"


def chain_to_dict(chain: Chain) -> dict[str, Any]:
    """JSON-serializable representation of the chain structure."""
    return {
        "kind": chain.kind,
        "pattern": chain.pattern.value,
        "agent_slug": chain.agent_slug,
        "step_count": len(chain.steps),
        "steps": [_step_to_dict(i, s, chain.agent_slug) for i, s in enumerate(chain.steps)],
    }


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------


def _step_node_body(step: Step) -> str:
    """Return the Mermaid suffix for a step node (shape + label)."""
    label = _step_label(step)
    if isinstance(step, CritiqueStep):
        return f'{{"{label}"}}'
    if isinstance(step, (CheckpointStep, ExecutionStep)):
        return f'[/"{label}"/]'
    # TaskStep, LLMStep, or any future auto-progressing kind
    return f'["{label}"]'


def _step_label(step: Step) -> str:
    """Build the multi-line label that appears inside a step node."""
    prefix = _kind_prefix(step)
    parts = [f"{prefix}: {_escape(step.summary)}"]
    if isinstance(step, CritiqueStep):
        parts.append(f"max {step.max_attempts} attempts")
    if isinstance(step, CheckpointStep) and step.on_approve_label:
        parts.append(f"on approve: {_escape(step.on_approve_label)}")
    return "<br/>".join(parts)


def _kind_prefix(step: Step) -> str:
    if isinstance(step, TaskStep):
        return "Task"
    if isinstance(step, LLMStep):
        return "LLM"
    if isinstance(step, CritiqueStep):
        return "Critique"
    if isinstance(step, CheckpointStep):
        return "Checkpoint"
    if isinstance(step, ExecutionStep):
        return "Execution"
    return step.step_kind.value


def _escape(text: str) -> str:
    """Make a string safe inside a Mermaid double-quoted label."""
    return text.replace('"', "&quot;")


def _step_to_dict(index: int, step: Step, default_agent_slug: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "index": index,
        "kind": step.step_kind.value,
        "summary": step.summary,
        "agent_slug": step.agent_slug or default_agent_slug,
        "action_type": step.action_type,
        "risk_level": step.risk_level,
        "has_skip_if": step.skip_if is not None,
        "skip_if_label": step.skip_if_label,
        "on_approve_label": step.on_approve_label,
        "has_on_approve_callback": (
            isinstance(step, CheckpointStep) and step._on_approve is not None
        ),
    }
    if isinstance(step, CritiqueStep):
        out["critiques_step_index"] = step.critiques_step_index
        out["max_attempts"] = step.max_attempts
    return out
