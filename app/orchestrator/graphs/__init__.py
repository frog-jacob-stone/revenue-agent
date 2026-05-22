"""Graph registry for the LangGraph runner.

Adding a new workflow kind: import its module here and call
`runner.register(kind, factory)` in `register_all()`. App startup runs
`register_all()` after `runner.init()` has built the AsyncPostgresSaver
checkpointer.
"""
from __future__ import annotations

from app.orchestrator.graphs import outreach
from app.orchestrator.runner import Runner


def register_all(runner: Runner) -> None:
    runner.register("outreach_chain", outreach.build_graph)
    # content_creation migrated to a tool in plan 16 (ADR-0002).
    # content_publish migrated to a tool + executor in plan 17 (ADR-0002).
    # rev_rec_monthly migrated to a tool + executor in plan 18 (ADR-0002).
