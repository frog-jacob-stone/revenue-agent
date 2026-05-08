from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.models.common import ORMBase


class ApprovalCreate(ORMBase):
    workflow_id: UUID
    node_name: str
    agent_slug: str
    action_type: str
    proposed_payload: dict[str, Any]
    summary: str | None = None
    reasoning: str | None = None
    risk_level: str | None = None
    assigned_to: str | None = None


class ApprovalApprove(ORMBase):
    approved_by: str = "system"
    executed_payload: dict[str, Any] | None = None


class ApprovalReject(ORMBase):
    rejected_by: str = "system"
    rejection_reason: str


class ApprovalResponse(ORMBase):
    id: UUID
    workflow_id: UUID
    node_name: str
    agent_slug: str
    action_type: str
    status: str
    risk_level: str | None = None
    summary: str | None = None
    reasoning: str | None = None
    proposed_payload: dict[str, Any]
    executed_payload: dict[str, Any] | None = None
    assigned_to: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejection_reason: str | None = None
    executed_at: datetime | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
