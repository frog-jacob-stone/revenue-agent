"""Graph registry for the v2 LangGraph runner.

Each phase of the LangGraph migration adds an import + a `runner.register(...)`
call here. `register_all()` runs at app startup, after `runner.init()` has
built the AsyncPostgresSaver checkpointer.
"""
from __future__ import annotations

from app.orchestrator.graphs import (
    content_creation,
    content_publish,
    outreach,
    rev_rec,
)
from app.orchestrator.runner import V2Runner


def register_all(runner: V2Runner) -> None:
    runner.register("content_publish", content_publish.build_graph)
    runner.register("rev_rec_monthly", rev_rec.build_graph)
    runner.register("outreach_chain", outreach.build_graph)
    runner.register("content_creation", content_creation.build_graph)
