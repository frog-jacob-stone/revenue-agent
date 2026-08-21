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

    # Billing / invoicing. Operator-initiated throughout (ADR-0004): there is no
    # approval row behind any of these, so this vocabulary is the whole record of
    # who authorized what. The INVOICE_* events below cover the Harvest write.
    BILLING_SNAPSHOT_REFRESHED = "billing.snapshot.refreshed"
    BILLING_GROUP_CREATED = "billing.group.created"
    BILLING_GROUP_UPDATED = "billing.group.updated"
    BILLING_GROUP_DEACTIVATED = "billing.group.deactivated"
    BILLING_RUN_PLANNED = "billing.run.planned"
    BILLING_RUN_ABANDONED = "billing.run.abandoned"
    BILLING_ITEM_APPROVED = "billing.item.approved"
    BILLING_ITEM_UNAPPROVED = "billing.item.unapproved"
    BILLING_ITEM_OVERRIDDEN = "billing.item.overridden"
    # Placeholder resolution. The payload carries the amount, because this is
    # the one number on a recurring invoice that no config row can account for
    # — "who said August's hosting was $1,240" is the question worth answering
    # later. An omit is recorded just as loudly: it is a decision, not a gap.
    BILLING_PLACEHOLDER_RESOLVED = "billing.placeholder.resolved"
    BILLING_PLACEHOLDER_CLEARED = "billing.placeholder.cleared"
    BILLING_DRAW_RELEASED = "billing.draw.released"
    BILLING_DRAW_UNRELEASED = "billing.draw.unreleased"
    # Account-level billing settings. The payload carries the new value, because
    # this is invoice copy a client reads — "who changed the remit-to details, and
    # to what" is the question worth answering later.
    BILLING_SETTINGS_UPDATED = "billing.settings.updated"

    # Client exclusions. Account-wide, not billing-scoped — an excluded client
    # disappears from the Projects roster and from config reconciliation alike,
    # so the trail records the standing instruction rather than one screen's
    # filter. The reason rides along: "why is this client hidden" is the whole
    # question a year later.
    CLIENT_EXCLUDED = "client.excluded"
    CLIENT_EXCLUSION_REMOVED = "client.exclusion.removed"

    # Forecast. Read-only against the vendor; the event records that the local
    # delivery-forecast cache was rebuilt and what it found.
    FORECAST_SCHEDULE_REFRESHED = "forecast.schedule.refreshed"

    # The Harvest write (PRD §8). Four outcomes, and the trail must distinguish
    # them: ATTEMPTED is written and committed *before* the POST, so an invoice
    # created during an outage that never returned still has a record on our side.
    # UNKNOWN is the ambiguous one — a timeout or 5xx where the invoice may or may
    # not exist. It is never inferred to be a failure.
    BILLING_INVOICE_ATTEMPTED = "billing.invoice.attempted"
    BILLING_INVOICE_CREATED = "billing.invoice.created"
    BILLING_INVOICE_FAILED = "billing.invoice.failed"
    BILLING_INVOICE_UNKNOWN = "billing.invoice.unknown"
    # Human resolution of an UNKNOWN outcome: either linking the invoice that did
    # get created, or recording that nothing did.
    BILLING_INVOICE_RESOLVED_LINKED = "billing.invoice.resolved.linked"
    BILLING_INVOICE_RESOLVED_FAILED = "billing.invoice.resolved.failed"


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

BILLING_SNAPSHOT_REFRESHED = AuditEvent.BILLING_SNAPSHOT_REFRESHED
BILLING_GROUP_CREATED = AuditEvent.BILLING_GROUP_CREATED
BILLING_GROUP_UPDATED = AuditEvent.BILLING_GROUP_UPDATED
BILLING_GROUP_DEACTIVATED = AuditEvent.BILLING_GROUP_DEACTIVATED
BILLING_RUN_PLANNED = AuditEvent.BILLING_RUN_PLANNED
BILLING_RUN_ABANDONED = AuditEvent.BILLING_RUN_ABANDONED
BILLING_ITEM_APPROVED = AuditEvent.BILLING_ITEM_APPROVED
BILLING_ITEM_UNAPPROVED = AuditEvent.BILLING_ITEM_UNAPPROVED
BILLING_ITEM_OVERRIDDEN = AuditEvent.BILLING_ITEM_OVERRIDDEN
BILLING_PLACEHOLDER_RESOLVED = AuditEvent.BILLING_PLACEHOLDER_RESOLVED
BILLING_PLACEHOLDER_CLEARED = AuditEvent.BILLING_PLACEHOLDER_CLEARED
BILLING_DRAW_RELEASED = AuditEvent.BILLING_DRAW_RELEASED
BILLING_DRAW_UNRELEASED = AuditEvent.BILLING_DRAW_UNRELEASED
BILLING_SETTINGS_UPDATED = AuditEvent.BILLING_SETTINGS_UPDATED
CLIENT_EXCLUDED = AuditEvent.CLIENT_EXCLUDED
CLIENT_EXCLUSION_REMOVED = AuditEvent.CLIENT_EXCLUSION_REMOVED
FORECAST_SCHEDULE_REFRESHED = AuditEvent.FORECAST_SCHEDULE_REFRESHED
BILLING_INVOICE_ATTEMPTED = AuditEvent.BILLING_INVOICE_ATTEMPTED
BILLING_INVOICE_CREATED = AuditEvent.BILLING_INVOICE_CREATED
BILLING_INVOICE_FAILED = AuditEvent.BILLING_INVOICE_FAILED
BILLING_INVOICE_UNKNOWN = AuditEvent.BILLING_INVOICE_UNKNOWN
BILLING_INVOICE_RESOLVED_LINKED = AuditEvent.BILLING_INVOICE_RESOLVED_LINKED
BILLING_INVOICE_RESOLVED_FAILED = AuditEvent.BILLING_INVOICE_RESOLVED_FAILED
