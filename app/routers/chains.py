"""Read-only API for the orchestrator's chain registry.

Three endpoints:
  GET /chains              — list all registered chains (optionally filter by agent)
  GET /chains/{kind}       — full structure as JSON
  GET /chains/{kind}/diagram — Mermaid flowchart source as text/plain
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.models.chains import ChainStep, ChainStructure, ChainSummary
from app.orchestrator.chain import _REGISTRY, get_chain
from app.orchestrator.diagram import chain_to_dict, chain_to_mermaid

router = APIRouter(prefix="/chains", tags=["chains"])


@router.get("", response_model=list[ChainSummary])
async def list_chains(
    agent_slug: str | None = Query(None, description="Filter to chains where the default agent_slug matches"),
) -> list[ChainSummary]:
    """List every registered chain. The agent_slug filter matches a chain's
    default agent only — per-step agent overrides are surfaced in the
    structure endpoint, not here."""
    chains = list(_REGISTRY.values())
    if agent_slug:
        chains = [c for c in chains if c.agent_slug == agent_slug]
    return [
        ChainSummary(
            kind=c.kind,
            pattern=c.pattern.value,
            agent_slug=c.agent_slug,
            step_count=len(c.steps),
        )
        for c in sorted(chains, key=lambda c: c.kind)
    ]


@router.get("/{kind}", response_model=ChainStructure)
async def get_chain_structure(kind: str) -> ChainStructure:
    try:
        chain = get_chain(kind)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Chain '{kind}' not registered")
    d = chain_to_dict(chain)
    return ChainStructure(
        kind=d["kind"],
        pattern=d["pattern"],
        agent_slug=d["agent_slug"],
        step_count=d["step_count"],
        steps=[ChainStep(**s) for s in d["steps"]],
    )


@router.get("/{kind}/diagram", response_class=PlainTextResponse)
async def get_chain_diagram(kind: str) -> str:
    try:
        chain = get_chain(kind)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Chain '{kind}' not registered")
    return chain_to_mermaid(chain)
