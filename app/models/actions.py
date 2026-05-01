from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import Field

from app.models.common import ORMBase


class ActionType(str, Enum):
    # The Python source of truth. The Postgres enum (created in migration 0001 +
    # 0003 + 0004) holds additional historical values like `generate_invoice`,
    # `research`, etc. that no producer writes anymore. Removing those from the
    # DB enum requires recreating the enum, so they stay orphaned.
    send_email = "send_email"
    write_rev_rec = "write_rev_rec"
    configure_rev_rec_projects = "configure_rev_rec_projects"
    post_to_linkedin = "post_to_linkedin"
    other = "other"


class ActionStatus(str, Enum):
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"
    executing = "executing"
    completed = "completed"
    failed = "failed"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ActionCreate(ORMBase):
    agent_slug: str
    action_type: ActionType
    summary: str
    proposed_payload: dict[str, Any]
    reasoning: str | None = None
    risk_level: RiskLevel | None = None


class ActionApprove(ORMBase):
    approved_by: str = "system"
    executed_payload: dict[str, Any] | None = None


class ActionReject(ORMBase):
    rejected_by: str = "system"
    rejection_reason: str


class StepKind(str, Enum):
    tool_call = "tool_call"
    llm_step = "llm_step"
    critique = "critique"
    checkpoint = "checkpoint"
    execution = "execution"


class ActionResponse(ORMBase):
    id: UUID
    workflow_id: UUID
    agent_id: UUID
    sequence: int
    action_type: ActionType
    status: ActionStatus
    summary: str
    proposed_payload: dict[str, Any]
    executed_payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    reasoning: str | None = None
    risk_level: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejection_reason: str | None = None
    executed_at: datetime | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    step_kind: StepKind | None = None
    parent_action_id: UUID | None = None
    retry_of_action_id: UUID | None = None
    attempt_number: int = 1
    max_attempts: int | None = None
    critique_result: dict[str, Any] | None = None
