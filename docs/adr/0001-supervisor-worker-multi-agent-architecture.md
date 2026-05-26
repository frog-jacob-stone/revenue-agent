# Supervisor-worker hierarchy as the primary multi-agent pattern

The system is built around a supervisor-worker hierarchy. The orchestrator agent (`chief-of-staff`) is the single interface to the user and routes work to domain agents via `ask_agent`. Domain agents own their tools and run autonomously — they decide which tools to call, in what order — using a ReAct loop driven by a new `run_agent_task` primitive in `agent_invoke.py`. The orchestrator stays thin as capabilities grow; domain agents evolve independently.

## Why not put all tools on the orchestrator

The alternative — attaching every capability as a tool on `chief-of-staff` — produces an agent with unbounded tool sprawl. Each new feature adds a tool; the orchestrator accumulates domain knowledge it shouldn't own; and the system becomes impossible to extend without changing the front door. Routing via `ask_agent` keeps the orchestrator's surface area fixed at the number of domain agents, not the number of capabilities.

## Considered options

**Option discarded: tool-per-feature on chief-of-staff.** Works at small scale but doesn't survive a multi-domain system. Rejected because the tool list grows with every feature rather than staying bounded.

**Option discarded: prescribed graph per feature.** LangGraph graphs are the right shape for deterministic, multi-step processes (revenue recognition, content critique loop) where steps should only change through explicit code changes. They are the wrong shape for exploratory or drafting tasks where the agent should decide the approach. Requiring a graph for every capability defeats the purpose of having intelligent agents.

## Consequences

- `ask_agent` must detect whether the target agent has `allowed_tools` and, if so, drive a ReAct loop via `run_agent_task` rather than a single-turn `invoke_agent` call.
- Domain agents that are autonomous must be `ConversationalAgent` subclasses (to use `get_tools()` / `execute_tool()`) or `BaseAgent` subclasses with `allowed_tools` declared — `run_agent_task` resolves the system prompt and tool list from the agent class.
- Prescribed workflows (LangGraph graphs) remain the right shape for fixed processes and are not replaced by this pattern.
