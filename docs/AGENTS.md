# Agents

The roster of agents in the system. Update when an agent is added, retired, or changes scope.

For the rules that govern how agents are bounded, see `ARCHITECTURE.md` (Agent Scoping Principles).

## Roster

| Agent | Trigger | Approval Required? |
|---|---|---|
| SDR Researcher | New account in HubSpot / manual | Before outreach |
| Outreach Agent | Manual ("Reach out" on dashboard) / SDR Researcher completes | Always — before any email sends |
| Voice Critic (Phase E) | Invoked by Outreach chain | n/a — internal critic step |
| Accuracy Critic (Phase E) | Invoked by Outreach chain | n/a — internal critic step |
| Content Writer | Manual or scheduled | Before publishing |
| Proposal Generator | Deal stage change in HubSpot | Before sending to client |
| Slide Deck Agent | Triggered by Proposal Generator | Before delivery |
| Revenue Recognition | Monthly schedule | Before any log write |
| Invoice Operations | HubSpot webhook / schedule / chat | Always — before generate, edit, send, or digest |
| Invoice Analytics | Chat (read-only) | Never (proposes no actions) |
| Router (future) | Chat | No (proposes no actions, just routes) |

> Each business domain is expected to have both an operations agent (write-proposing) and an analytics agent (read-only) as the roster matures. Revenue Recognition, for example, will eventually have a Revenue Analytics sibling.

## Prompts and SOUL Definitions

_Agent prompt templates and behavioral rules live alongside the agent code in `app/agents/<agent>/`. Add references here as agents stabilize._

## Revenue Recognition (orchestrated)

Implemented as a `supervised_automation` chain in [`app/orchestrator/chains/rev_rec.py`](../app/orchestrator/chains/rev_rec.py). Trigger: monthly cron, the chat tool `trigger_revenue_recognition`, or `RevenueRecognitionAgent.trigger()` directly.

| Seq | step_kind | What it does | Skipped when |
|---|---|---|---|
| 0 | tool_call | Sync Harvest → Airtable, fetch projects, validate completeness | — |
| 1 | tool_call | Compute revenue entries (per-project Forecast + Harvest invoice totals) | validation incomplete |
| 2 | checkpoint | Configure incomplete projects (action_type `configure_rev_rec_projects`) | validation passed |
| 3 | execution | Write entries to Airtable (action_type `write_rev_rec`) | validation incomplete |

The "fix incomplete projects then keep going" loop is realised via the checkpoint's `on_approve` callback — approving the checkpoint queues a fresh `rev_rec_monthly` workflow so the user can iterate until validation passes. This replaces the previous special case that lived in `app/services/execution.py`.

`RevenueRecognitionAgent.run()` is no longer used (raises `NotImplementedError`); all execution flows through the orchestrator.

## Outreach Agent (Phase D — happy path)

Implemented as an orchestrator chain in [`app/orchestrator/chains/outreach.py`](../app/orchestrator/chains/outreach.py). Pattern: `prompt_chain_action`. Trigger: `POST /workflows/outreach { "hubspot_contact_id": "..." }` or the "Reach out" button on the Outreach Agent card on the dashboard.

Chain steps:

| Seq | step_kind | What it does |
|---|---|---|
| 1 | tool_call | Pull HubSpot contact + company (stubbed when `HUBSPOT_TOKEN` is unset) |
| 2 | tool_call | Web search company signals (stub for Phase D — Apollo/web search ships later) |
| 3 | llm_step  | Consolidate context into a brief |
| 4 | tool_call | Retrieve Frogslayer GTM context (stub blurb until pgvector ingestion lands) |
| 5 | llm_step  | Draft outreach email (subject + body) |
| 6 | execution | Approve and send via Gmail (stub: logs `[gmail-stub] would send …`) |

Phase E will insert voice and accuracy critique steps with retry loops between steps 5 and 6.

When `ANTHROPIC_API_KEY` is unset, the LLM steps fall back to deterministic stub responses so the chain runs end-to-end in dev environments without creds.
