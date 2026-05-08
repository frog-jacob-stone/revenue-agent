"""Step kinds for orchestrated chains.

A Step is a node in a chain. Each step kind has different semantics:
  - task, llm_step, critique : auto-progress; orchestrator runs them inline
  - checkpoint, execution    : pause for human approval; resume runs execute()

Concrete behavior is supplied by handler callables passed in at construction so
chains can be defined declaratively without subclassing.

Each step optionally takes a `skip_if` predicate that the orchestrator evaluates
before running the step. When `skip_if` returns True the orchestrator advances
without writing any action row — this is how chains express conditional paths
("write to Airtable if validation passed; ask for fixes otherwise").
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, ClassVar

from app.models.actions import StepKind
from app.orchestrator.state import StepContext

# A handler returns the dict written to actions.result (and proposed_payload).
# For checkpoint/execution, propose_payload returns proposed_payload (what the
# human reviews); execute_handler is called after approval and returns the
# operation result (what actually happened).
PropHandler = Callable[[StepContext], Awaitable[dict[str, Any]]]
ExecHandler = Callable[[StepContext], Awaitable[dict[str, Any]]]
# A skip predicate is evaluated synchronously against StepContext.
SkipPredicate = Callable[[StepContext], bool]


class Step(ABC):
    """Base step. Subclasses bind a step_kind and provide propose/execute behavior."""

    step_kind: ClassVar[StepKind]

    def __init__(
        self,
        summary: str,
        *,
        agent_slug: str | None = None,
        action_type: str = "other",
        risk_level: str | None = None,
        skip_if: SkipPredicate | None = None,
        skip_if_label: str | None = None,
        on_approve_label: str | None = None,
    ) -> None:
        self.summary = summary
        # If None, the orchestrator falls back to the chain's default agent.
        self.agent_slug = agent_slug
        # action_type is required NOT NULL on actions; orchestrated steps default
        # to "other" since the action_type enum is oriented around legacy executors.
        self.action_type = action_type
        self.risk_level = risk_level
        # When set and returns True for the current StepContext, the orchestrator
        # skips this step (no action row written) and advances current_step.
        self.skip_if = skip_if
        # Human-readable labels for the diagram visualizer. The predicate and
        # callback themselves are opaque Python; these document the intent.
        self.skip_if_label = skip_if_label
        self.on_approve_label = on_approve_label

    @abstractmethod
    async def propose(self, ctx: StepContext) -> dict[str, Any]:
        """Compute proposed_payload (and result for auto-progressing kinds)."""
        ...

    async def execute(self, ctx: StepContext) -> dict[str, Any]:
        """Run the side effect after approval. Default: no-op (for checkpoints)."""
        return {}


class TaskStep(Step):
    """Auto-progressing step that runs deterministic code: integration calls,
    data fetching, validation, computation. No LLM, no tool selection — just
    code-driven work. Distinct from `LLMStep` (which calls an LLM) and from
    the chat-agent tools in `app/tools/` (which use the LLM tool-use protocol)."""

    step_kind: ClassVar[StepKind] = StepKind.task

    def __init__(self, summary: str, handler: PropHandler, **kwargs: Any) -> None:
        super().__init__(summary, **kwargs)
        self._handler = handler

    async def propose(self, ctx: StepContext) -> dict[str, Any]:
        return await self._handler(ctx)


class LLMStep(Step):
    """Auto-progressing LLM call (drafting, consolidating, revising)."""

    step_kind: ClassVar[StepKind] = StepKind.llm_step

    def __init__(self, summary: str, handler: PropHandler, **kwargs: Any) -> None:
        super().__init__(summary, **kwargs)
        self._handler = handler

    async def propose(self, ctx: StepContext) -> dict[str, Any]:
        return await self._handler(ctx)


class CritiqueStep(Step):
    """Auto-progressing evaluator. Handler must return a critique result dict
    shaped like {"passed": bool, "score": float, "feedback": str, "issues": []}.

    On fail (with budget remaining), the orchestrator writes a retry of the
    critiqued step and re-runs from there. On budget exhausted, the workflow
    is marked failed.
    """

    step_kind: ClassVar[StepKind] = StepKind.critique

    def __init__(
        self,
        summary: str,
        handler: PropHandler,
        *,
        critiques_step_index: int,
        max_attempts: int,
        **kwargs: Any,
    ) -> None:
        super().__init__(summary, **kwargs)
        self._handler = handler
        self.critiques_step_index = critiques_step_index
        self.max_attempts = max_attempts

    async def propose(self, ctx: StepContext) -> dict[str, Any]:
        return await self._handler(ctx)


class CheckpointStep(Step):
    """Pauses the workflow for a human review.

    By default, approval is a pure gate — no side effect runs on resume. Pass
    an `on_approve` callback to do something on approval (e.g. requeue a new
    workflow because the human just fixed external data).
    """

    step_kind: ClassVar[StepKind] = StepKind.checkpoint

    def __init__(
        self,
        summary: str,
        propose_payload: PropHandler | None = None,
        *,
        on_approve: ExecHandler | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(summary, **kwargs)
        # Optional: pre-populate proposed_payload (e.g. with the draft to review).
        # If None, payload is built from the prior step's result.
        self._propose_payload = propose_payload
        self._on_approve = on_approve

    async def propose(self, ctx: StepContext) -> dict[str, Any]:
        if self._propose_payload is not None:
            return await self._propose_payload(ctx)
        # Default: surface the most recent prior step's result for review.
        prior = ctx.state.latest_for_step(ctx.step_index - 1) if ctx.step_index > 0 else None
        return prior.result if prior and prior.result else {}

    async def execute(self, ctx: StepContext) -> dict[str, Any]:
        if self._on_approve is not None:
            return await self._on_approve(ctx)
        return {}


class ExecutionStep(Step):
    """Pauses for approval, then performs an external write on resume."""

    step_kind: ClassVar[StepKind] = StepKind.execution

    def __init__(
        self,
        summary: str,
        executor: ExecHandler,
        propose_payload: PropHandler | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(summary, **kwargs)
        self._executor = executor
        self._propose_payload = propose_payload

    async def propose(self, ctx: StepContext) -> dict[str, Any]:
        if self._propose_payload is not None:
            return await self._propose_payload(ctx)
        prior = ctx.state.latest_for_step(ctx.step_index - 1) if ctx.step_index > 0 else None
        return prior.result if prior and prior.result else {}

    async def execute(self, ctx: StepContext) -> dict[str, Any]:
        return await self._executor(ctx)
