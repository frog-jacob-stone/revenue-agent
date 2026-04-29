# Backlog

What's next, what's deferred, what's blocked, and what's still being decided. Update this when work starts, finishes, or shifts.

## Now

Agentic Workflow Patterns build (six phases). Phases A, B, C, and D landed:

- **Phase A** — Migration `0005_agentic_patterns.sql`, inbox filter on `step_kind`, Pydantic models updated.
- **Phase B** — `app/orchestrator/` module with `PromptChainOrchestrator`, five step kinds, chain registry, BackgroundTasks-based resume hook, rejection→cancel for orchestrated workflows.
- **Phase C** — `GET /workflows/{id}/trace` endpoint plus reusable `<WorkflowTrace workflowId={id} />` component in the inbox detail view. Renders all chain steps in `sequence` order with status icons, step_kind labels, retry attempt counters, and inline-collapsible critique results.
- **Phase D** — Outreach chain (`app/orchestrator/chains/outreach.py`) with 6 steps: HubSpot/web/KB tool_calls, two LLM steps (consolidate, draft), and an execution step that gates on human approval before the (stubbed) Gmail send. `POST /workflows/outreach` trigger endpoint and "Reach out" button on the dashboard. LLM steps fall back to deterministic stubs when `ANTHROPIC_API_KEY` is unset; HubSpot fetch falls back to placeholder data when `HUBSPOT_TOKEN` is unset. Refactored `orchestrator.start` into `create_workflow` + `resume` so the trigger endpoint returns 202 immediately.

Remaining phases: E (Critique loops) → F (Tree UI + smoke doc).

## Next

Planned next, in rough order:

1. **Migrate Revenue Recognition off the re-trigger hack** — convert it to a `supervised_automation` chain through the orchestrator (originally part of Phase D; deferred as separate scope to keep the Outreach PR focused).
2. Phase E — Voice + accuracy critique loops with `voice-critic` and `accuracy-critic` agents.
3. Phase F — Tree-rendering trace UI (sibling retries grouped under their root); `docs/SMOKE_TEST_OUTREACH.md`.
4. Wire real HubSpot / Gmail / Apollo (web search) integrations to remove the stubs in the outreach chain.

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
