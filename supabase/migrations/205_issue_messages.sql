-- 205_issue_messages.sql
--
-- Paperclip-style chat thread per issue (A8 data layer).
--
-- Three message kinds living in one timeline:
--   'comment'        — user or agent posts text/markdown
--   'agent_run'      — summary of an agent_run that fired on this issue,
--                      with body=markdown summary + duration_seconds, and
--                      agent_run_id FK to the underlying run
--   'system_status'  — status transition marker (from_status → to_status),
--                      no body, surfaced as inline event in the chat feed
--
-- RLS cascades through `issues`: if the user can SELECT the issue, they
-- can SELECT its messages. INSERT is restricted to authenticated users
-- writing as themselves OR service_role (which is what the agent
-- runtime uses to record agent_run / system_status events).

BEGIN;

-- ─────────────────────────────────────────────────────────────────
-- 1) agent_runs.issue_id — link a run back to the issue that
--    triggered it. Optional (background sweepers / scheduled runs
--    have no issue), so SET NULL on delete.
-- ─────────────────────────────────────────────────────────────────
ALTER TABLE public.agent_runs
  ADD COLUMN IF NOT EXISTS issue_id BIGINT REFERENCES public.issues(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_agent_runs_issue
  ON public.agent_runs(issue_id) WHERE issue_id IS NOT NULL;

COMMENT ON COLUMN public.agent_runs.issue_id IS
  'Optional FK back to issues. Set when an agent run was dispatched
   from an issue (paperclip-style "reply triggers agent"). Surfaced
   in issue_messages.kind=agent_run for the chat thread.';

-- ─────────────────────────────────────────────────────────────────
-- 2) issue_messages — the chat thread table
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.issue_messages (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  issue_id         BIGINT NOT NULL REFERENCES public.issues(id) ON DELETE CASCADE,
  kind             TEXT   NOT NULL CHECK (kind IN ('comment','agent_run','system_status')),

  -- Author. For 'comment': either user or agent. For 'agent_run': agent.
  -- For 'system_status': both NULL when triggered by the system / trigger
  -- (status mirror), or author_user_id set when a user moved the issue.
  author_user_id   UUID,
  author_agent_id  UUID REFERENCES public.ai_agents(id) ON DELETE SET NULL,

  -- Markdown body (comment / agent_run summary). NULL for system_status.
  body             TEXT,

  -- Free-form metadata (board signoff IDs, attachments, mentions, etc).
  meta             JSONB NOT NULL DEFAULT '{}',

  -- agent_run-specific fields
  duration_seconds INT,
  agent_run_id     UUID REFERENCES public.agent_runs(id) ON DELETE SET NULL,

  -- system_status-specific fields
  from_status      TEXT,
  to_status        TEXT,

  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Sanity: only one of (user, agent) author for 'comment'/'agent_run';
  -- 'system_status' allows either none or user.
  CONSTRAINT issue_messages_author_chk CHECK (
    CASE kind
      WHEN 'comment'        THEN author_user_id IS NOT NULL OR author_agent_id IS NOT NULL
      WHEN 'agent_run'      THEN author_agent_id IS NOT NULL
      WHEN 'system_status'  THEN author_agent_id IS NULL
      ELSE FALSE
    END
  ),
  CONSTRAINT issue_messages_status_chk CHECK (
    (kind = 'system_status') = (from_status IS NOT NULL OR to_status IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_issue_messages_issue_created
  ON public.issue_messages(issue_id, created_at);
CREATE INDEX IF NOT EXISTS idx_issue_messages_run
  ON public.issue_messages(agent_run_id) WHERE agent_run_id IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────
-- 3) RLS — cascades through issues
-- ─────────────────────────────────────────────────────────────────
ALTER TABLE public.issue_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS issue_messages_select ON public.issue_messages;
CREATE POLICY issue_messages_select ON public.issue_messages
  FOR SELECT TO authenticated
  USING (EXISTS (
    SELECT 1 FROM public.issues i WHERE i.id = issue_messages.issue_id
  ));

DROP POLICY IF EXISTS issue_messages_insert ON public.issue_messages;
CREATE POLICY issue_messages_insert ON public.issue_messages
  FOR INSERT TO authenticated
  WITH CHECK (
    EXISTS (SELECT 1 FROM public.issues i WHERE i.id = issue_messages.issue_id)
    AND (
      -- User-authored comment posting as themselves
      (kind = 'comment' AND author_user_id = auth.uid()) OR
      -- User-authored status change
      (kind = 'system_status' AND (author_user_id = auth.uid() OR author_user_id IS NULL))
    )
  );

DROP POLICY IF EXISTS issue_messages_service_full ON public.issue_messages;
CREATE POLICY issue_messages_service_full ON public.issue_messages
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ─────────────────────────────────────────────────────────────────
-- 4) Realtime
-- ─────────────────────────────────────────────────────────────────
DO $$
BEGIN
  PERFORM 1 FROM pg_publication_tables
  WHERE pubname = 'supabase_realtime' AND tablename = 'issue_messages';
  IF NOT FOUND THEN
    EXECUTE 'ALTER PUBLICATION supabase_realtime ADD TABLE public.issue_messages';
  END IF;
EXCEPTION WHEN undefined_object THEN
  NULL;
END $$;

ALTER TABLE public.issue_messages REPLICA IDENTITY FULL;

-- ─────────────────────────────────────────────────────────────────
-- 5) Status-change trigger: when issues.status changes, emit a
--    system_status message into the chat thread automatically.
-- ─────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.emit_issue_status_change_message()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    INSERT INTO public.issue_messages
      (issue_id, kind, author_user_id, from_status, to_status, meta)
    VALUES
      (NEW.id, 'system_status',
       -- Best-effort author: prefer the user who triggered the update.
       -- auth.uid() returns NULL when the update came via service_role
       -- (e.g., agent worker); leave author_user_id NULL in that case.
       auth.uid(),
       OLD.status, NEW.status,
       jsonb_build_object('source', 'trigger'));
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_issue_status_change_message ON public.issues;
CREATE TRIGGER trg_issue_status_change_message
AFTER UPDATE OF status ON public.issues
FOR EACH ROW
EXECUTE FUNCTION public.emit_issue_status_change_message();

-- ─────────────────────────────────────────────────────────────────
-- 6) Comments
-- ─────────────────────────────────────────────────────────────────
COMMENT ON TABLE public.issue_messages IS
  'Paperclip-style chat thread per issue (A8). Three kinds in one timeline:
   comment / agent_run / system_status. RLS cascades through issues:
   anyone who can SELECT the issue can SELECT its messages. Trigger
   trg_issue_status_change_message auto-emits a system_status row on
   any issues.status update.';

COMMENT ON FUNCTION public.emit_issue_status_change_message() IS
  'AFTER UPDATE OF status trigger on issues. Inserts a system_status
   row into issue_messages so the chat thread shows the status
   transition inline. SECURITY DEFINER because the trigger may fire
   under a role that lacks INSERT on issue_messages directly.';

COMMIT;
