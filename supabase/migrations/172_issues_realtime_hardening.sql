-- 172: PR-D2.1 — issues realtime publication hardening
--
-- Closes /review finding #3 (HIGH): migration 168 added issues to
-- supabase_realtime publication but didn't set REPLICA IDENTITY FULL and
-- didn't add a realtime SELECT policy. As written, anyone subscribed
-- receives every issue mutation including assignee_user_id, description,
-- and execution_state (which can hold agent secrets / OAuth tokens).
--
-- Fixes:
--   1. The supabase realtime broker (postgres_changes) re-checks the same
--      RLS SELECT policy for each subscriber, so the existing issues_select
--      policy DOES filter — but only if the subscriber connects with a
--      user JWT (anon/authenticated). Service-role subscribers receive
--      everything. Document this explicitly.
--   2. Drop execution_state from published columns — it can contain LLM
--      tool-call payloads, OAuth tokens, file paths, etc. Per-row column
--      filter via supabase_realtime's column allowlist.
--
-- Note on REPLICA IDENTITY: deliberately left at the default (PRIMARY KEY).
-- REPLICA IDENTITY FULL would include the entire row in OLD on DELETE/UPDATE,
-- but PG rejects FULL combined with a column-allowlist publication
-- ("Column list used by the publication does not cover the replica identity"
-- error). DEFAULT (PK) is sufficient: realtime subscribers receive the new
-- column values on UPDATE and the PK on DELETE, which is what the issues UI
-- needs.

-- Restrict the publication to a column allowlist that excludes
-- execution_state (and dbos_workflow_id, which is internal plumbing).
-- This requires re-adding the table with a column list since PG publication
-- doesn't have a partial-column-update syntax.
DO $$
BEGIN
  -- Check if issues is in the publication; drop and re-add with column filter
  IF EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename = 'issues'
  ) THEN
    ALTER PUBLICATION supabase_realtime DROP TABLE public.issues;
  END IF;

  -- Re-add with explicit column list (PG 15+ feature; supabase NAS dev
  -- runs PG 17.6 so this is supported).
  ALTER PUBLICATION supabase_realtime ADD TABLE public.issues
    (id, issue_number, identifier, team_id, project_id, parent_id,
     title, description, status, priority,
     assignee_user_id, assignee_agent_id,
     created_by_user_id, created_by_agent_id,
     origin_kind, origin_id, request_depth,
     billing_code,
     started_at, completed_at, cancelled_at, hidden_at,
     created_at, updated_at);
  -- Excluded: execution_state, execution_locked_at, dbos_workflow_id, goal_id, origin_fingerprint
END
$$;

COMMENT ON TABLE public.issues IS
  'Top-level user-visible "thing". DBOS workflows reference issues.id via dbos_workflow_id and issues.dbos_workflow_id reciprocally. Schema ported from Paperclip (MIT) with mediahub adaptations. Realtime publication excludes execution_state / execution_locked_at / dbos_workflow_id (internal plumbing or sensitive); see migration 172.';
