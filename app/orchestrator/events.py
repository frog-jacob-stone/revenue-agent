"""Canonical audit event names emitted by orchestrator.

Every audit event written from a v2 node, runner, approval flow, or
sub-workflow spawn MUST use one of these constants. Adding a new event
type is a deliberate change — extend this module, document it in
docs/SCHEMA.md under the audit_log section, and use the new constant.
"""

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
