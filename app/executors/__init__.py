"""Executor layer (ADR-0002).

Executors are the writer functions invoked by the approval-grant handler
AFTER a human approves an `AwaitingApproval`. They are NOT LLM-callable
and are NEVER added to any agent's `allowed_tools`. The structural
separation enforces Unbreakable Rule #3: approvals are human-only.

Adding a new executor:
  1. Create `app/executors/<name>.py` with an `ExecutorDefinition` constant
  2. Import and add it to `EXECUTORS` in `app/executors/registry.py`
"""
from app.executors.base import ExecutorContext, ExecutorDefinition

__all__ = ["ExecutorContext", "ExecutorDefinition"]
