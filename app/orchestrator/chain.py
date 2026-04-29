"""Chain definitions and registry.

A Chain is an ordered list of Steps for a given workflow `kind`. Chains are
registered at import time; the orchestrator looks them up by kind.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.workflows import WorkflowPattern
from app.orchestrator.steps import Step


@dataclass(frozen=True)
class Chain:
    kind: str
    pattern: WorkflowPattern
    agent_slug: str  # default agent for steps that don't override
    steps: tuple[Step, ...]


_REGISTRY: dict[str, Chain] = {}


def register_chain(chain: Chain) -> Chain:
    if chain.kind in _REGISTRY:
        raise RuntimeError(f"Chain already registered for kind '{chain.kind}'")
    _REGISTRY[chain.kind] = chain
    return chain


def get_chain(kind: str) -> Chain:
    if kind not in _REGISTRY:
        raise KeyError(f"No chain registered for workflow kind '{kind}'")
    return _REGISTRY[kind]


def has_chain(kind: str) -> bool:
    return kind in _REGISTRY


def _reset_registry_for_tests() -> None:
    """Test helper: clear the registry. Not for production use."""
    _REGISTRY.clear()
