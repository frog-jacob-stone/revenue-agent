"""orchestrator — LangGraph-based orchestration runtime.

Public surface:
  runner: Runner               — the singleton; exposes start/resume/register
  events                       — canonical audit event constants
  invoke_agent, NodeContext    — agent invocation primitive
  spawn_workflow               — sub-workflow primitive
"""
from app.orchestrator import events
from app.orchestrator.agent_invoke import NodeContext, invoke_agent, run_agent_task
from app.orchestrator.runner import GraphSpec, Runner, runner
from app.orchestrator.spawn import spawn_workflow

__all__ = [
    "GraphSpec",
    "NodeContext",
    "Runner",
    "events",
    "invoke_agent",
    "run_agent_task",
    "runner",
    "spawn_workflow",
]
