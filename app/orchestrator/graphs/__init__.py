"""Graph registry for the LangGraph runner.

Adding a new workflow kind: import its module here and call
`runner.register(kind, factory)` in `register_all()`. App startup runs
`register_all()` after `runner.init()` has built the AsyncPostgresSaver
checkpointer.
"""
from __future__ import annotations

from app.orchestrator.graphs import (
    content_creation,
    content_publish,
    outreach,
    rev_rec,
)
from app.orchestrator.runner import Runner


def register_all(runner: Runner) -> None:
    runner.register("content_publish", content_publish.build_graph)
    runner.register("rev_rec_monthly", rev_rec.build_graph)
    runner.register("outreach_chain", outreach.build_graph)
    runner.register("content_creation", content_creation.build_graph)
