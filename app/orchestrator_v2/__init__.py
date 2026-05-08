"""orchestrator_v2 — LangGraph-based orchestration runtime.

Lives next to the legacy orchestrator (`app/orchestrator/`) during the
multi-agent rearchitecture. Master plan:
.agent/plans/3.langgraph-multi-agent-rearchitecture.md

Public surface:
  runner: V2Runner             — the singleton; exposes start/resume/register
  events                       — canonical audit event constants
  invoke_agent, NodeContext    — agent invocation primitive
  spawn_workflow               — sub-workflow primitive
"""
from app.orchestrator_v2 import events
from app.orchestrator_v2.agent_invoke import NodeContext, invoke_agent
from app.orchestrator_v2.runner import GraphSpec, V2Runner, runner
from app.orchestrator_v2.spawn import spawn_workflow

__all__ = [
    "GraphSpec",
    "NodeContext",
    "V2Runner",
    "events",
    "invoke_agent",
    "runner",
    "spawn_workflow",
]
