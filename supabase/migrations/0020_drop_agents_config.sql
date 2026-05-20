-- agents.config was a free-form jsonb knob that nothing in app code ever
-- read. Per-agent LLM selection lives on the BaseAgent.model class attribute
-- (see app/agents/*.py). Follows the precedent of 0006_simplify_agents.sql:
-- DB stores runtime-mutable state only.
alter table agents
  drop column if exists config;
