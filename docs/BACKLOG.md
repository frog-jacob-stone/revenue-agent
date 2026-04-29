# Backlog

What's next, what's deferred, what's blocked, and what's still being decided. Update this when work starts, finishes, or shifts.

## Now

Agentic Workflow Patterns build is complete. All six phases plus the Rev Rec migration have shipped.

- **Phase A** — Migration `0005_agentic_patterns.sql`, inbox filter on `step_kind`, Pydantic models updated.
- **Phase B** — `app/orchestrator/` module with `PromptChainOrchestrator`, five step kinds, chain registry, BackgroundTasks-based resume hook, rejection→cancel.
- **Phase C** — `GET /workflows/{id}/trace` endpoint and reusable `<WorkflowTrace>` component in the inbox detail.
- **Phase D** — Outreach chain (happy path) with `POST /workflows/outreach` trigger and "Reach out" dashboard button.
- **Rev Rec migration** — `rev_rec_monthly` chain replaces the `execution.py` re-trigger hack; introduces `skip_if` step predicates and `CheckpointStep.on_approve`.
- **Phase E** — Voice + accuracy critique loops in the Outreach chain. New `voice-critic` and `accuracy-critic` agents (registered via `_CriticAgent` base in `app/agents/planned.py`). Voice profile lives in `memories` (`kind=preference`, `metadata.kind=voice_profile`), seeded by `seed.seed_voice_profile()`. Failed critiques rewind to the draft step; exhaustion marks workflow `failed`.
- **Phase F** — `WorkflowTrace` upgraded to tree-grouped rendering: retry attempts indent under their root, latest attempt highlighted, originals dimmed/strikethrough. Default state is collapsed with a one-line summary. `docs/SMOKE_TEST_OUTREACH.md` documents the manual end-to-end run.

## Next

1. Wire real HubSpot / Gmail / Apollo (web search) integrations to remove the stubs in the outreach chain.
2. Document ingestion pipeline (SharePoint → parse → chunk → embed → `knowledge_base`) — required for pattern #3.
3. Brand research workflow (pattern #3, `prompt_chain_artifact`) — needs ingestion pipeline plus a follow-up migration for output_artifact_url and possibly a `claims` table.
4. Real worker queue (Arq + Redis) for orchestrator resume — needed before chains can survive server restarts reliably.
5. Multi-gate workflows (multiple checkpoints in one chain) — schema supports it; the trace UI will need light polish.

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
