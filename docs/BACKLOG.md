# Backlog

What's next, what's deferred, what's blocked, and what's still being decided. Update this when work starts, finishes, or shifts.

## Now

_(In-flight work — what Claude or Jacob is actively building.)_

## Next

Planned next, in rough order:

1. Move complex n8n logic to FastAPI — revenue recognition first
2. Expand agent roster from the foundation already built
3. Invoice Operations agent + Invoice Analytics agent (first domain with both write and read agents)

## Later

- Router agent (gated on 4+ specialists existing and observed routing friction)
- Shared memory patterns between agents (agents leaving notes for each other via `memories` table)
- Full orchestrator for multi-step cross-agent workflows
- Cloud deployment (Railway / Render)

## Blocked

_(Empty — add items as they get blocked, with the blocker.)_

## Open Questions

- CrewAI / LangGraph for multi-agent orchestration vs. direct Anthropic SDK calls
- Vector search strategy for the knowledge base (pgvector vs. dedicated store)
- Role-based approvals as the team grows (who approves what)
- When to introduce the router agent (threshold: ~4–6 specialists + observed routing friction)
- Memory sharing scope between agents (per-agent vs. shared-by-domain vs. fully global)

## Done

_(Recently completed work worth remembering. Trim periodically.)_

- Supabase schema — agent memory, action queue, audit log tables
- FastAPI skeleton — one endpoint per agent
- Approval inbox UI — shared across agents
- SDR Researcher agent (first end-to-end agent)
- Audit log API + UI
- Harvest integration tools + Revenue Recognition agent foundation
- Invoice Operations agent (generation path disabled — see CLAUDE.md unbreakable rule)
