-- Single front-door agent. The chat surface only serves `revenue-ops`;
-- the column is kept (forward-only — easier reversal than a drop) and
-- gains a default so new sessions don't need to pass agent_slug.
ALTER TABLE chat_sessions
  ALTER COLUMN agent_slug SET DEFAULT 'revenue-ops';
