# Agents

The roster of agents in the system. Update when an agent is added, retired, or changes scope.

For the rules that govern how agents are bounded, see `ARCHITECTURE.md` (Agent Scoping Principles).

## Roster

| Agent | Trigger | Approval Required? |
|---|---|---|
| Outreach Agent | Manual ("Reach out" on dashboard) | Always — before any email sends |
| Voice Critic | Invoked by Outreach chain | n/a — internal critic step |
| Accuracy Critic | Invoked by Outreach chain | n/a — internal critic step |
| Revenue Recognition | Monthly schedule / chat | Before any log write |
| Content Orchestrator | Chat (`POST /chat/content-orchestrator`) | No for creation/editing — publish_post queues in approval inbox |
| Personal Voice Agent | Invoked by content_creation chain | n/a — internal critique step |
| Router (future) | Chat | No (proposes no actions, just routes) |

> Each business domain is expected to have both an operations agent (write-proposing) and an analytics agent (read-only) as the roster matures.

## Content Orchestrator

Implemented as a `ConversationalAgent` in [`app/agents/content.py`](../app/agents/content.py). Trigger: `POST /chat/content-orchestrator`.

The orchestrator is the single interface for all content work: brainstorming ideas (pure LLM chat, nothing persisted), triggering creation, editing, and publishing.

**Tools (6):**

| Tool | What it does |
|---|---|
| `create_post` | Triggers the `content_creation` chain — brief → idea → draft → voice review. Returns when `ready` or `needs_revision`. |
| `get_posts` | Returns posts filtered by status |
| `rewrite_post` | Rewrites post per user instruction; resets status to `draft` |
| `reject_post` | Sets status = `rejected` |
| `publish_post` | Triggers the `content_publish` chain — queues post in approval inbox before anything is posted |
| `export_posts` | Returns `ready` + `published` posts as clean copy/paste text |

**Internal sub-agents** (prompt holders, registered in DB for CritiqueStep FK; not accessible via chat):

| Class | Role |
|---|---|
| `ContentStrategyAgent` | Generates one idea (title, core_angle) for a topic |
| `LinkedInWritingAgent` | Drafts a post from an idea; enforces voice and formatting rules |
| `PersonalVoiceAgent` | Reviews a draft against Jacob's voice profile; channel-aware for future reuse |

**Status flow for `social_posts`:**
```
draft → needs_revision → draft → ready → published
      └──────── rewrite resets to draft ──────────┘
         rejected (user rejected via chat)
```

**Conversational commands the orchestrator understands:**
- "Give me some ideas about X" → LLM responds inline; no tool call
- "Draft a post about X" / "Draft from idea 2"
- "Create 3 posts about X"
- "Show me posts that need revision"
- "Change the hook" / "Make it more direct" / any edit instruction
- "Reject post 2"
- "Post this" / "Publish post 2"
- "Export posts"

> **Recently retired:** Invoice Operations + Invoice Analytics agents (and the SDR Researcher / Content Writer / Proposal Generator / Slide Deck Agent stub classes that had no working implementation). They will be re-introduced when their use cases land. See `docs/BACKLOG.md`.

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

## Outreach Agent

Implemented as an orchestrator chain in [`app/orchestrator/chains/outreach.py`](../app/orchestrator/chains/outreach.py). Pattern: `prompt_chain_action`. Trigger: `POST /workflows/outreach { "hubspot_contact_id": "..." }` or the "Reach out" button on the Outreach Agent card on the dashboard.

Chain steps:

| Seq | step_kind | Agent | What it does |
|---|---|---|---|
| 1 | tool_call | outreach-agent | Pull HubSpot contact + company (stubbed when `HUBSPOT_TOKEN` is unset) |
| 2 | tool_call | outreach-agent | Web search company signals (stub) |
| 3 | llm_step  | outreach-agent | Consolidate context into a brief |
| 4 | tool_call | outreach-agent | Retrieve Frogslayer GTM context (stub blurb until pgvector ingestion lands) |
| 5 | llm_step  | outreach-agent | Draft outreach email — receives critique feedback on retry |
| 6 | critique  | voice-critic | Voice critique against the seeded voice profile (max_attempts=3) |
| 7 | critique  | accuracy-critic | Cross-check claims against upstream context (max_attempts=2) |
| 8 | execution | outreach-agent | Approve and send via Gmail (stub: logs `[gmail-stub] would send …`) |

Both critiques retry the draft step (idx 4) on failure; an exhausted budget marks the workflow `failed`. Successful critiques advance to the human approval gate.

When `ANTHROPIC_API_KEY` is unset, the LLM steps and critique steps fall back to deterministic stub responses (critics default to PASS) so the chain runs end-to-end in dev environments without creds.

### Voice Critic + voice profile memory

Voice Critic pulls its evaluation criteria from a `preference` memory tagged `metadata.kind = 'voice_profile'` and seeded by [`app/seed.py::seed_voice_profile()`](../app/seed.py) on app startup. The seed is idempotent and never overwrites a manually edited row. To update the voice profile, edit the memory directly in `memories` — no deploy required. The default profile lives in `app/seed.py` as `_VOICE_PROFILE_TEXT`.

### Accuracy Critic

Reads the draft plus all upstream context (HubSpot record, web signals, brief) and flags any factual claim in the draft that isn't supported by the context. No external memory or knowledge base lookups — accuracy is judged against the chain's own data.
