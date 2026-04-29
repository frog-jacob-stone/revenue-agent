# Backlog

What's next, what's deferred, what's blocked, and what's still being decided. Update this when work starts, finishes, or shifts.

## Now

Agentic Workflow Patterns build. Phases A–D landed plus the Rev Rec migration:

- **Phase A** — Migration `0005_agentic_patterns.sql`, inbox filter on `step_kind`, Pydantic models updated.
- **Phase B** — `app/orchestrator/` module with `PromptChainOrchestrator`, five step kinds, chain registry, BackgroundTasks-based resume hook, rejection→cancel for orchestrated workflows.
- **Phase C** — `GET /workflows/{id}/trace` endpoint plus reusable `<WorkflowTrace workflowId={id} />` component in the inbox detail view.
- **Phase D** — Outreach chain (`app/orchestrator/chains/outreach.py`) with 6 steps and a `POST /workflows/outreach` trigger.
- **Rev Rec migration** — `app/orchestrator/chains/rev_rec.py` (`supervised_automation` pattern). Two divergent endings (configure-incomplete-projects checkpoint vs write-entries execution) live in one chain via new `skip_if` step predicates. `CheckpointStep.on_approve` fires when the user approves an incomplete-projects checkpoint, queuing a fresh validation cycle — replaces the special case that lived in `app/services/execution.py`. `RevenueRecognitionAgent.trigger()` now routes through the orchestrator; `run()` is no longer used.

Remaining phases: E (Critique loops) → F (Tree UI + smoke doc).

## Next

Planned next, in rough order:

1. Phase E — Voice + accuracy critique loops with `voice-critic` and `accuracy-critic` agents.
2. Phase F — Tree-rendering trace UI (sibling retries grouped under their root); `docs/SMOKE_TEST_OUTREACH.md`.
3. Wire real HubSpot / Gmail / Apollo (web search) integrations to remove the stubs in the outreach chain.

## Later

- Router agent (gated on 4+ specialists existing and observed routing friction)
- Shared memory patterns between agents (agents leaving notes for each other via `memories` table)
- **Real worker queue (Arq + Redis)** for orchestrator resume — needed before chains can reliably survive server restarts. Phase B uses FastAPI BackgroundTasks; lost-on-restart resumes are recovered via a manual "Resume" button.
- **Document ingestion pipeline** (SharePoint → parse → chunk → embed → `knowledge_base`) — required before brand research workflow.
- **Brand research workflow (pattern #3, `prompt_chain_artifact`)** — heavy RAG, claim-level traceability, ends in a file artifact. Needs ingestion pipeline first plus a follow-up migration (likely `output_artifact_url` and possibly a `claims` table).
- **Multi-gate workflows** — workflows with multiple checkpoints in one chain. Schema supports it; trace UI doesn't visualize it well yet.
- **Parallelism in chains** — running tool calls concurrently. Will require relaxing the strict `sequence` ordering convention.
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
