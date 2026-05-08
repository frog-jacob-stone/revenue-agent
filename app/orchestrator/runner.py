"""V2 runner — drives LangGraph StateGraph execution and bridges with the
`approvals` table for human-in-the-loop pauses.

Phase 1 swaps `MemorySaver` → `AsyncPostgresSaver`. Graph state now persists
across restarts. The checkpointer holds its own psycopg pool against
`settings.database_url` (separate from the app's asyncpg pool) — psycopg is
what `langgraph-checkpoint-postgres` requires.

Init is lazy: `_ensure_init()` runs on first `start`/`resume`. This keeps
the module importable in tests that don't touch the DB and avoids tying the
singleton to FastAPI lifespan ordering.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

import asyncpg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings
from app.db import get_pool
from app.orchestrator import events
from app.services import approvals as approvals_service
from app.services import audit

logger = logging.getLogger(__name__)


# A factory returns the uncompiled StateGraph, plus the list of nodes that
# require human approval before execution.
GraphFactory = Callable[[], "GraphSpec"]


@dataclass(frozen=True)
class GraphSpec:
    """What a graph factory returns: the graph plus which nodes need approval."""

    graph: StateGraph
    interrupt_before: tuple[str, ...] = ()


class V2Runner:
    """Owns the registered graphs and the shared LangGraph checkpointer.

    Public surface:
      register(kind, factory) — wire a graph kind to its factory
      is_registered(kind) — used by trigger endpoints to dispatch v1 vs v2
      init() — explicit checkpointer setup; safe to call multiple times
      start(kind, ...) — create a workflow row + drive the graph
      resume(workflow_id) — drive a paused graph forward after approval

    Registration stores the factory; the graph is compiled lazily once
    `init()` has built the checkpointer. Tests can register before init.
    """

    def __init__(self) -> None:
        self._compiled: dict[str, CompiledStateGraph] = {}
        self._registrations: dict[str, GraphFactory] = {}
        self._checkpointer: AsyncPostgresSaver | None = None
        self._cp_pool: AsyncConnectionPool | None = None
        self._initialized: bool = False
        self._init_lock = asyncio.Lock()

    # ── Init ─────────────────────────────────────────────────────────────────

    async def init(self) -> None:
        """Build the AsyncPostgresSaver and compile any pre-registered graphs.

        Idempotent. Called from app lifespan (eager) and from start/resume
        (lazy fallback so tests work without the FastAPI app).
        """
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            self._cp_pool = AsyncConnectionPool(
                conninfo=settings.database_url,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                },
                min_size=1,
                max_size=10,
                open=False,
            )
            await self._cp_pool.open()
            self._checkpointer = AsyncPostgresSaver(self._cp_pool)
            await self._checkpointer.setup()
            for kind in list(self._registrations):
                self._compile(kind)
            self._initialized = True
            logger.info("v2_runner: initialized AsyncPostgresSaver checkpointer")

    async def _ensure_init(self) -> None:
        if not self._initialized:
            await self.init()

    # ── Registration ─────────────────────────────────────────────────────────

    def register(self, kind: str, factory: GraphFactory) -> None:
        if kind in self._registrations:
            raise ValueError(f"graph kind already registered: {kind}")
        self._registrations[kind] = factory
        if self._initialized:
            self._compile(kind)
        logger.info("v2_runner: registered kind=%s", kind)

    def _compile(self, kind: str) -> None:
        spec = self._registrations[kind]()
        self._compiled[kind] = spec.graph.compile(
            checkpointer=self._checkpointer,
            interrupt_before=list(spec.interrupt_before) or None,
        )

    def unregister(self, kind: str) -> None:
        """Test helper. Production code should not call this."""
        self._compiled.pop(kind, None)
        self._registrations.pop(kind, None)

    def is_registered(self, kind: str) -> bool:
        return kind in self._registrations

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(
        self,
        kind: str,
        *,
        initial_state: dict[str, Any],
        initiated_by: str = "system",
        trigger_source: str = "manual",
        subject_type: str | None = None,
        subject_id: str | None = None,
        subject_ref: dict[str, Any] | None = None,
        parent_workflow_id: UUID | None = None,
    ) -> UUID:
        """Insert a workflow row, then drive the graph until interrupt or completion.

        Returns the new workflow_id.
        """
        if kind not in self._registrations:
            raise ValueError(f"graph kind not registered: {kind}")
        await self._ensure_init()

        pool = await get_pool()
        workflow_id = await self._create_workflow_row(
            pool,
            kind=kind,
            initial_state=initial_state,
            initiated_by=initiated_by,
            trigger_source=trigger_source,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_ref=subject_ref,
            parent_workflow_id=parent_workflow_id,
        )

        seeded = {**initial_state, "workflow_id": str(workflow_id)}
        if parent_workflow_id is not None:
            seeded["parent_workflow_id"] = str(parent_workflow_id)
        await self._drive(workflow_id, kind, seeded)
        return workflow_id

    async def resume(self, workflow_id: UUID | str) -> None:
        """Resume a paused graph. Idempotent — does nothing for terminal workflows.

        Approved approvals: run forward with the (possibly edited) payload.
        Rejected approvals: mark the workflow failed and stop.
        Pending approvals: nothing to do.
        """
        wf_id = UUID(str(workflow_id))
        pool = await get_pool()
        wf = await pool.fetchrow(
            "SELECT id, kind, status FROM workflows WHERE id = $1", wf_id
        )
        if wf is None:
            logger.warning("v2_runner.resume: workflow %s not found", wf_id)
            return
        if wf["status"] in ("completed", "failed", "cancelled"):
            return

        kind = wf["kind"]
        if kind not in self._registrations:
            return  # not a v2 workflow — let v1 handle it
        await self._ensure_init()

        # Look at the most recent approval for this workflow to decide what to do.
        latest = await pool.fetchrow(
            """
            SELECT * FROM approvals
            WHERE workflow_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            wf_id,
        )
        if latest is None:
            # No approval yet — first start() should have driven it. Nothing to do.
            return

        if latest["status"] == "pending":
            return  # still waiting on a human
        if latest["status"] == "rejected":
            await self._mark_workflow_failed(
                pool, wf_id, error=f"approval rejected: {latest['rejection_reason']}"
            )
            return
        if latest["status"] in ("executed", "failed"):
            return  # already resumed past this approval

        # status == 'approved' → drive forward with executed_payload as input.
        async with pool.acquire() as conn:
            await audit.write_audit_event(
                conn,
                events.WORKFLOW_RESUMED,
                workflow_id=wf_id,
                actor="orchestrator",
                payload={"approval_id": str(latest["id"])},
            )

        executed_payload = latest["executed_payload"] or latest["proposed_payload"] or {}
        await self._drive(
            wf_id,
            kind,
            seeded_state={"executed_payload": executed_payload},
            resuming=True,
            current_approval_id=latest["id"],
        )

    # ── Internals ────────────────────────────────────────────────────────────

    async def _drive(
        self,
        workflow_id: UUID,
        kind: str,
        seeded_state: dict[str, Any],
        *,
        resuming: bool = False,
        current_approval_id: UUID | None = None,
    ) -> None:
        """Step the graph forward until it interrupts or completes.

        Emits node.entered/exited events as the graph progresses, then either
        creates an approval row (interrupt) or marks the workflow completed.
        """
        compiled = self._compiled[kind]
        config = {"configurable": {"thread_id": str(workflow_id)}}
        pool = await get_pool()

        # If we're resuming after an approval, push the executed_payload into
        # graph state before invoking. The graph reads it from state.
        if resuming:
            try:
                await compiled.aupdate_state(config, seeded_state)
            except Exception as exc:
                logger.exception("v2_runner: aupdate_state failed for %s", workflow_id)
                await self._mark_workflow_failed(pool, workflow_id, error=str(exc))
                return
            invoke_input: dict[str, Any] | None = None
        else:
            invoke_input = seeded_state

        try:
            # Stream so we can emit node-level audit events. Each chunk is
            # `{node_name: state_update}`.
            async for chunk in compiled.astream(invoke_input, config=config):
                for node_name, _state_update in chunk.items():
                    async with pool.acquire() as conn:
                        await audit.write_audit_event(
                            conn,
                            events.NODE_EXITED,
                            workflow_id=workflow_id,
                            actor=f"orchestrator:{node_name}",
                            payload={"node": node_name},
                        )
        except Exception as exc:
            logger.exception("v2_runner: graph execution failed for %s", workflow_id)
            async with pool.acquire() as conn:
                await audit.write_audit_event(
                    conn,
                    events.NODE_FAILED,
                    workflow_id=workflow_id,
                    actor="orchestrator",
                    payload={"error": str(exc)},
                )
            await self._mark_workflow_failed(pool, workflow_id, error=str(exc))
            if current_approval_id is not None:
                await approvals_service.mark_failed(pool, current_approval_id, str(exc))
            return

        # After streaming completes, check if we paused at an interrupt or finished.
        snapshot = await compiled.aget_state(config)
        next_nodes: tuple[str, ...] = tuple(snapshot.next or ())
        state_values: dict[str, Any] = dict(snapshot.values or {})

        if next_nodes:
            # Paused — write an approval row from state["_propose"] if present.
            await self._on_pause(
                pool, workflow_id, kind, next_nodes, state_values
            )
        else:
            # Graph terminated cleanly.
            if current_approval_id is not None:
                await approvals_service.mark_executed(pool, current_approval_id)
            await self._mark_workflow_completed(pool, workflow_id)

    async def _on_pause(
        self,
        pool: asyncpg.Pool,
        workflow_id: UUID,
        kind: str,
        next_nodes: tuple[str, ...],
        state_values: dict[str, Any],
    ) -> None:
        propose = state_values.get("_propose") or {}
        node_name = next_nodes[0] if next_nodes else "<unknown>"

        async with pool.acquire() as conn:
            async with conn.transaction():
                await approvals_service.create_pending_conn(
                    conn,
                    workflow_id=workflow_id,
                    node_name=propose.get("node_name", node_name),
                    agent_slug=propose.get("agent_slug", "system"),
                    action_type=propose.get("action_type", "other"),
                    proposed_payload=propose.get("proposed_payload", {}),
                    summary=propose.get("summary"),
                    reasoning=propose.get("reasoning"),
                    risk_level=propose.get("risk_level"),
                    assigned_to=propose.get("assigned_to"),
                )
                await audit.write_audit_event(
                    conn,
                    events.WORKFLOW_PAUSED,
                    workflow_id=workflow_id,
                    actor=f"orchestrator:{kind}",
                    payload={"interrupt_before": node_name},
                )
                await conn.execute(
                    "UPDATE workflows SET status = 'awaiting_approval' WHERE id = $1",
                    workflow_id,
                )

    # ── DB helpers ───────────────────────────────────────────────────────────

    async def _create_workflow_row(
        self,
        pool: asyncpg.Pool,
        *,
        kind: str,
        initial_state: dict[str, Any],
        initiated_by: str,
        trigger_source: str,
        subject_type: str | None,
        subject_id: str | None,
        subject_ref: dict[str, Any] | None,
        parent_workflow_id: UUID | None,
    ) -> UUID:
        async with pool.acquire() as conn:
            async with conn.transaction():
                workflow_id = await conn.fetchval(
                    """
                    INSERT INTO workflows
                        (kind, status,
                         trigger_source, trigger_payload,
                         subject_type, subject_id, subject_ref,
                         initiated_by, parent_workflow_id)
                    VALUES ($1, 'running', $2, $3, $4, $5, $6, $7, $8)
                    RETURNING id
                    """,
                    kind,
                    trigger_source,
                    initial_state,
                    subject_type,
                    subject_id,
                    subject_ref,
                    initiated_by,
                    parent_workflow_id,
                )
                await audit.write_audit_event(
                    conn,
                    events.WORKFLOW_STARTED,
                    workflow_id=workflow_id,
                    actor=initiated_by,
                    payload={
                        "kind": kind,
                        "parent_workflow_id": str(parent_workflow_id) if parent_workflow_id else None,
                    },
                )
        return workflow_id

    async def _mark_workflow_completed(self, pool: asyncpg.Pool, workflow_id: UUID) -> None:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE workflows
                    SET status = 'completed', completed_at = now()
                    WHERE id = $1 AND status NOT IN ('completed','failed','cancelled')
                    """,
                    workflow_id,
                )
                await audit.write_audit_event(
                    conn,
                    events.WORKFLOW_COMPLETED,
                    workflow_id=workflow_id,
                    actor="orchestrator",
                )

    async def _mark_workflow_failed(
        self, pool: asyncpg.Pool, workflow_id: UUID, *, error: str
    ) -> None:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE workflows
                    SET status = 'failed', completed_at = now()
                    WHERE id = $1 AND status NOT IN ('completed','failed','cancelled')
                    """,
                    workflow_id,
                )
                await audit.write_audit_event(
                    conn,
                    events.WORKFLOW_FAILED,
                    workflow_id=workflow_id,
                    actor="orchestrator",
                    payload={"error": error},
                )


# Module-level singleton. Imported by the router and graph factories.
runner = V2Runner()
