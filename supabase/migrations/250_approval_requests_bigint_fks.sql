-- Migration 250: agent_approval_requests.{session_id,run_id} UUID → BIGINT
--
-- Closes the #408 follow-up. ai_sessions.id (mig 231) and agent_runs.id
-- (mig 232) became BIGINT Snowflake, but agent_approval_requests.session_id
-- and .run_id were never converted — they're still UUID columns (mig 198).
-- The app layer (#408) now writes the bigint string id into them, which fails
-- 22P02 on a UUID column. The table currently has 0 rows and no FK on these
-- two columns (verified on prod), so a plain ALTER TYPE is safe and instant.
--
-- id / agent_id / user_id stay UUID (those are genuinely uuid: the approval's
-- own PK, the agent's uuid id, and the auth.users FK).
--
-- USING NULL::bigint is safe because the table is empty; if any rows existed
-- they could not hold a valid uuid that's also a bigint anyway.

ALTER TABLE public.agent_approval_requests
  ALTER COLUMN session_id TYPE bigint USING NULL::bigint;

ALTER TABLE public.agent_approval_requests
  ALTER COLUMN run_id TYPE bigint USING NULL::bigint;
