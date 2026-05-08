"""Pydantic models for the /chains API.

Read-only views of the orchestrator's in-memory chain registry. No DB.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ChainSummary(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    kind: str
    pattern: str
    agent_slug: str
    step_count: int


class ChainStep(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    index: int
    kind: str
    summary: str
    agent_slug: str
    action_type: str
    risk_level: str | None = None
    has_skip_if: bool = False
    skip_if_label: str | None = None
    on_approve_label: str | None = None
    has_on_approve_callback: bool = False
    critiques_step_index: int | None = None
    max_attempts: int | None = None


class ChainStructure(ChainSummary):
    steps: list[ChainStep]
