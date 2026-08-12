"""Orchestrator runtime: dispatch tools, invoke agents, audit events.

Post-ADR-0002 contents:
  dispatch_tool                — tool runtime (Done | AwaitingApproval | Blocked)
  run_agent_task, NodeContext  — agent invocation (ReAct loop or single-turn)
  events                       — canonical audit event constants
"""
from app.orchestrator import events
from app.orchestrator.agent_invoke import NodeContext, run_agent_task
from app.orchestrator.dispatch import dispatch_tool

__all__ = [
    "NodeContext",
    "dispatch_tool",
    "events",
    "run_agent_task",
]
