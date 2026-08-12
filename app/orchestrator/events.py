"""Canonical audit event names emitted by orchestrator.

Every audit event written from a node, runner, approval flow, or
sub-workflow spawn MUST use one of these constants. Adding a new event
type is a deliberate change — extend `AuditEvent`, document it in
docs/SCHEMA.md under the audit_log section, and use the new member.

The constants below are the public API; callers continue to import them
as `events.WORKFLOW_STARTED`. They are typed members of `AuditEvent` so
`write_audit_event`'s signature can enforce membership.
"""
from __future__ import annotations

from enum import StrEnum


class AuditEvent(StrEnum):
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"
    WORKFLOW_PAUSED = "workflow.paused"
    WORKFLOW_RESUMED = "workflow.resumed"

    NODE_ENTERED = "node.entered"
    NODE_EXITED = "node.exited"
    NODE_FAILED = "node.failed"

    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_EXECUTED = "approval.executed"
    APPROVAL_FAILED = "approval.failed"

    AGENT_INVOKED = "agent.invoked"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"

    SUBWORKFLOW_SPAWNED = "subworkflow.spawned"
    SUBWORKFLOW_COMPLETED = "subworkflow.completed"

    CHAT_TURN_STARTED = "chat.turn.started"
    CHAT_TURN_COMPLETED = "chat.turn.completed"
    CHAT_TURN_FAILED = "chat.turn.failed"

    # Tool-level events (ADR-0002). Replace workflow/node events as graphs are
    # migrated to tools. APPROVAL_REQUESTED / APPROVAL_EXECUTED / APPROVAL_FAILED
    # are reused for the AwaitingApproval -> execute chain.
    TOOL_CALLED = "tool.called"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    TOOL_BLOCKED = "tool.blocked"


WORKFLOW_STARTED = AuditEvent.WORKFLOW_STARTED
WORKFLOW_COMPLETED = AuditEvent.WORKFLOW_COMPLETED
WORKFLOW_FAILED = AuditEvent.WORKFLOW_FAILED
WORKFLOW_PAUSED = AuditEvent.WORKFLOW_PAUSED
WORKFLOW_RESUMED = AuditEvent.WORKFLOW_RESUMED

NODE_ENTERED = AuditEvent.NODE_ENTERED
NODE_EXITED = AuditEvent.NODE_EXITED
NODE_FAILED = AuditEvent.NODE_FAILED

APPROVAL_REQUESTED = AuditEvent.APPROVAL_REQUESTED
APPROVAL_GRANTED = AuditEvent.APPROVAL_GRANTED
APPROVAL_REJECTED = AuditEvent.APPROVAL_REJECTED
APPROVAL_EXECUTED = AuditEvent.APPROVAL_EXECUTED
APPROVAL_FAILED = AuditEvent.APPROVAL_FAILED

AGENT_INVOKED = AuditEvent.AGENT_INVOKED
AGENT_COMPLETED = AuditEvent.AGENT_COMPLETED
AGENT_FAILED = AuditEvent.AGENT_FAILED

SUBWORKFLOW_SPAWNED = AuditEvent.SUBWORKFLOW_SPAWNED
SUBWORKFLOW_COMPLETED = AuditEvent.SUBWORKFLOW_COMPLETED

CHAT_TURN_STARTED = AuditEvent.CHAT_TURN_STARTED
CHAT_TURN_COMPLETED = AuditEvent.CHAT_TURN_COMPLETED
CHAT_TURN_FAILED = AuditEvent.CHAT_TURN_FAILED

TOOL_CALLED = AuditEvent.TOOL_CALLED
TOOL_COMPLETED = AuditEvent.TOOL_COMPLETED
TOOL_FAILED = AuditEvent.TOOL_FAILED
TOOL_BLOCKED = AuditEvent.TOOL_BLOCKED
