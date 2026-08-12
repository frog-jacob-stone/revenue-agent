-- Rename agent slugs in-place to reflect the new front-door layout.
--
--   - The old front-door `revenue-ops` becomes `chief-of-staff` (a thin
--     coordinator that delegates to domain agents).
--   - The old domain agent `revenue-recognition` becomes `revenue-ops`
--     (a true RevOps agent owning revenue tools and analysis).
--
-- The `agents` row UUID is preserved through both renames, so all FK-bound
-- audit history (audit_log.agent_id, approvals.agent_id, etc.) remains
-- correctly attributed without any UUID surgery.
--
-- The text `agent_slug` columns on dependent tables are rewritten so queries
-- using the current slug naming see consistent history.

BEGIN;

-- 1. Identity table — preserve UUIDs, rename slugs.
UPDATE agents SET slug = 'chief-of-staff' WHERE slug = 'revenue-ops';
UPDATE agents SET slug = 'revenue-ops'    WHERE slug = 'revenue-recognition';

-- 2. Text slug columns on dependent tables.
UPDATE chat_sessions  SET agent_slug = 'chief-of-staff' WHERE agent_slug = 'revenue-ops';
UPDATE chat_sessions  SET agent_slug = 'revenue-ops'    WHERE agent_slug = 'revenue-recognition';

UPDATE approvals      SET agent_slug = 'chief-of-staff' WHERE agent_slug = 'revenue-ops';
UPDATE approvals      SET agent_slug = 'revenue-ops'    WHERE agent_slug = 'revenue-recognition';

UPDATE llm_calls      SET agent_slug = 'chief-of-staff' WHERE agent_slug = 'revenue-ops';
UPDATE llm_calls      SET agent_slug = 'revenue-ops'    WHERE agent_slug = 'revenue-recognition';

UPDATE agent_messages SET from_agent_slug = 'chief-of-staff' WHERE from_agent_slug = 'revenue-ops';
UPDATE agent_messages SET to_agent_slug   = 'chief-of-staff' WHERE to_agent_slug   = 'revenue-ops';
UPDATE agent_messages SET from_agent_slug = 'revenue-ops'    WHERE from_agent_slug = 'revenue-recognition';
UPDATE agent_messages SET to_agent_slug   = 'revenue-ops'    WHERE to_agent_slug   = 'revenue-recognition';

-- 3. Update the chat_sessions default that migration 0019 set to 'revenue-ops'.
ALTER TABLE chat_sessions
  ALTER COLUMN agent_slug SET DEFAULT 'chief-of-staff';

COMMIT;
