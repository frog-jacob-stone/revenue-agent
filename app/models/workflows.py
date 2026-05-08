from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import Field

from app.models.common import ORMBase


class WorkflowStatus(str, Enum):
    pending = "pending"
    running = "running"
    awaiting_approval = "awaiting_approval"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class WorkflowCreate(ORMBase):
    kind: str
    trigger_source: str
    trigger_payload: dict[str, Any] | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    subject_ref: dict[str, Any] | None = None
    initiated_by: str = "system"
    metadata: dict[str, Any] | None = None


class WorkflowResponse(ORMBase):
    id: UUID
    kind: str
    status: WorkflowStatus
    trigger_source: str | None = None
    trigger_payload: dict[str, Any] | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    subject_ref: dict[str, Any] | None = None
    initiated_by: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceEvent(ORMBase):
    """Audit-log event for workflow tracing."""
    id: int
    event_type: str
    occurred_at: datetime
    actor: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowTraceResponse(ORMBase):
    workflow_id: UUID
    kind: str
    status: WorkflowStatus
    events: list[TraceEvent] = Field(default_factory=list)
