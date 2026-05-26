# Single `Agent` class; delegation allowlist is structural

All agents are instances of one `Agent` class. The `BaseAgent` / `ConversationalAgent` split is removed. Role (Orchestrator vs Domain, per [CONTEXT.md](../../CONTEXT.md)) lives in vocabulary and at the slug level, not in the type system. Per-caller delegation is declared as a `ClassVar[tuple[type[Agent], ...]]` named `available_agents` (symmetric with `allowed_tools` holding `ToolDefinition` references) and **enforced inside `ask_agent`** — the same "structural, not conventional" standard [ADR-0002](0002-tools-not-graphs.md) applies to executors.

This supersedes the class-hierarchy hedge in [ADR-0001](0001-supervisor-worker-multi-agent-architecture.md) ("Domain agents that are autonomous must be `ConversationalAgent` subclasses ... or `BaseAgent` subclasses with `allowed_tools` declared"). The supervisor-worker hierarchy itself (orchestrator routes via `ask_agent`; domain agents own tools and run via `run_agent_task`) is unchanged.

## Why collapse the hierarchy

The split was a marker, not a boundary. Auditing what the type distinction was actually doing:

- `_resolve_system_prompt` in `agent_invoke.py` duck-typed across the split with `try/except` — already a smell that the two shapes wanted to be one.
- `routers/agents.py` exposed `is_conversational` on the API response; no UI code read it.
- `chat_turn.py` already hardcoded `FRONT_DOOR_SLUG = ChiefOfStaffAgent.slug` — the chat path targets one slug directly; the `issubclass(ConversationalAgent)` check was redundant defense.
- The split tracked "is the orchestrator" but conflated it with "supports a dynamic system prompt." `BDRAgent` and other domain agents will plausibly want dynamic prompts in the future without becoming chattable front doors.

Carrying a two-class hierarchy for a marker that could be a flag — and an unused flag at that — is the wrong shape. Role is a CONTEXT.md vocabulary distinction (Orchestrator agent vs Domain agent) keyed to the slug `chief-of-staff`. The type system carries no invariants the registry doesn't already carry.

## Why structural delegation enforcement

[ADR-0002](0002-tools-not-graphs.md) sets the standard for executors: the LLM cannot reach them through any tool surface — not "by convention" but structurally, by living in a separate registry. The same logic applies to inter-agent delegation.

Status quo before this ADR: `ASK_AGENT` is in `ChiefOfStaffAgent.allowed_tools` (structural) but not in any domain agent's `allowed_tools`. So today there's a binary structural check ("does this agent have `ASK_AGENT` at all?") and no per-target restriction. The system-prompt sentence listing `bdr, revenue-ops, content-orchestrator` is advisory English — the LLM could hallucinate any slug and `run_agent_task` would dispatch it.

`available_agents` as a `ClassVar[tuple[type[Agent], ...]]`, enforced inside `_ask_agent`, closes the gap:

- The caller declares its delegation dependencies explicitly, analogous to `allowed_tools` holding `ToolDefinition` references. Slugs remain the wire contract for the LLM (`ask_agent("bdr", ...)`); class references are the authoring representation.
- `ASK_AGENT.execute` reads the caller's class via `ctx.agent_slug`, derives the allowed slug set as `{cls.slug for cls in caller_cls.available_agents}`, and returns `Blocked(reason=...)` if the target is not in it.
- The system prompt renders the same set (each target's slug + its `description` ClassVar) as soft guidance — but soft guidance can no longer be escaped by hallucination. The `description` itself doubles as the delegation guide ("Delegate when: ... Pass: ... Returns: ..."), so adding or modifying an agent updates the orchestrator's prompt automatically.

Forward-compatible for "BDR can ask `content-orchestrator` for follow-up subject lines but not `chief-of-staff`," and for loop prevention by structural omission (no agent has `chief-of-staff` in its `available_agents`).

## Considered options

**Option discarded: type hierarchy enforces role (`OrchestratorAgent` / `DomainAgent`).** Would let mypy catch role violations. Rejected because role is unlikely to stay binary — the user may eventually open chat to domain agents, sub-orchestrators are plausibly Domain agents with `ASK_AGENT`, and the taxonomy is mid-evolution. Type hierarchies ossify snapshots. And once we *also* retrofit `ask_agent`/executors to type-check role, the hierarchy adds little beyond a single class plus per-agent declarations.

**Option discarded: declare `available_agents` but only render it in the prompt; don't enforce in `ask_agent`.** Same "convention, not structural" anti-pattern [ADR-0002](0002-tools-not-graphs.md) rejects for executors. Half-measure.

**Option discarded: derive the delegation list (orchestrator can ask all non-self agents; no other agent can delegate).** Works only while delegation is uniform. Breaks the moment a Domain agent gets `ASK_AGENT`. Forces a re-architecture later for a benefit that's purely "fewer ClassVars today."

**Option discarded: per-target declaration (`callable_by` on the target).** Symmetric to caller-side but inverts control — the target declares who can call it, rather than the caller declaring its dependencies. Caller-side matches the existing `allowed_tools` shape and reads more naturally in the prompt rendering ("I can call these agents") than target-side would.

## Consequences

- `app/agents/base.py`: a single `Agent` class. `__init__(self, agent_id: UUID | None = None)`. `get_system_prompt(self) -> str` is the only system-prompt API; no `ClassVar` form. `available_agents: ClassVar[tuple[type[Agent], ...]] = ()` added. Per-instance `allowed_tools` override removed. No `ABC`.
- `ChiefOfStaffAgent`: `available_agents = (BDRAgent, RevenueOpsAgent, ContentOrchestratorAgent)`; `get_system_prompt()` renders the specialist roster from this tuple, pulling each class's `slug` and `description`. Each domain agent's `description` carries its own delegation guidance ("Delegate when / Pass / Returns"), so the orchestrator's prompt has no hand-maintained per-agent narrative section.
- `BDRAgent`, `ContentOrchestratorAgent`, `RevenueOpsAgent`: gain a `get_system_prompt(self) -> str` that returns their existing prompt constant. `available_agents` defaults to `()` — they cannot delegate.
- `app/agents/tools/agent/ask_agent.py`: `_ask_agent` looks up the caller via `ctx.agent_slug` and returns `Blocked(reason="…")` if `target_slug not in caller_cls.available_agents`.
- `app/orchestrator/agent_invoke.py`: `_resolve_system_prompt` deleted; inline `agent_cls(agent_id=agent_id).get_system_prompt()`.
- `app/services/chat_turn.py`: `_get_front_door()` simplifies to instantiating `ChiefOfStaffAgent(agent_id=…)` directly; the `issubclass` check is removed.
- `app/routers/agents.py`: `is_conversational` removed from `_enrich`.
- `app/models/agents.py`: Pydantic schema renamed `Agent` → `AgentRead` to free the runtime class name; `is_conversational` field removed.
- `ui/src/types.ts`: `is_conversational` field removed.
- `tests/test_chief_of_staff_agent.py`: `test_revenue_ops_is_conversational` deleted. `tests/test_tools.py`: the `allowed_tools=[]` override tests deleted along with the capability.
- Docstring/description cleanup across `app/agents/*.py`: "front-door agent" → "orchestrator agent"; "worker agent" → "domain agent". CONTEXT.md vocabulary was already correct; the code was the drift.
