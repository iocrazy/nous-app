-- 166: PR-D1 — issues table (Paperclip-port), atomic identifier counter, RLS.
--
-- Top-level user-visible "thing" entity introduced by the DBOS migration
-- (design doc Approach B). Replaces the implicit triple of
-- unified_tasks / agent_tasks / project_tasks for "the thing the user owns";
-- those tables stay as execution-layer detail rows linked back via issue_id.
--
-- Schema port from Paperclip with mediahub adaptations:
--   - id is BIGINT snowflake (mediahub convention since migration 051), not UUID
--   - identifier is single global prefix MH-N (no per-team scoping)
--   - assignee dual-fk (user OR agent) with application-level XOR enforcement
--   - origin_kind is open-ended (chat_delegate / agent_dispatch / routine / etc.)
--
-- Verified design decisions:
--   - PoC #10 (2026-04-28) — 6 RLS policies validated on _poc_issues
--   - PoC #11 (2026-04-28) — service_role / mediahub_dbos / authenticated boundary
--   - eng review 2026-04-27 — premises P6/P7/P8/P10
--
-- Idempotent: safe to re-run (DROP / CREATE OR REPLACE patterns).

-- =============================================================================
-- Extensions (Postgres trigram for full-text search; pgcrypto for gen_random_*)
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- =============================================================================
-- issue_sequence — single-row global counter
-- =============================================================================
-- We deliberately avoid PG sequences (nextval rollback-safe = leaves gaps).
-- An atomic UPDATE...RETURNING on this row guarantees no gaps and no duplicate
-- identifiers under concurrent INSERTs (PG row-level lock semantics).
CREATE TABLE IF NOT EXISTS public.issue_sequence (
  scope   TEXT PRIMARY KEY DEFAULT 'global',
  prefix  TEXT NOT NULL DEFAULT 'MH',
  counter INTEGER NOT NULL DEFAULT 0
);

INSERT INTO public.issue_sequence (scope, prefix, counter)
VALUES ('global', 'MH', 0)
ON CONFLICT (scope) DO NOTHING;

COMMENT ON TABLE public.issue_sequence IS
  'Atomic counter for issues.identifier (MH-N). Single-row table; UPDATE...RETURNING is the contract.';


-- =============================================================================
-- issues — top-level user-visible entity
-- =============================================================================
DROP TABLE IF EXISTS public.issues CASCADE;

CREATE TABLE public.issues (
  -- Identity (mediahub snowflake)
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),

  -- Numbering (set by issue_next_identifier() RPC; never updated)
  issue_number INTEGER NOT NULL,
  identifier   TEXT NOT NULL,                          -- 'MH-1042'

  -- Hierarchy
  team_id    BIGINT REFERENCES public.teams(id)    ON DELETE SET NULL,
  project_id BIGINT REFERENCES public.projects(id) ON DELETE SET NULL,
  parent_id  BIGINT REFERENCES public.issues(id)   ON DELETE SET NULL,
  goal_id    BIGINT,  -- M2 placeholder; no FK yet

  -- Content
  title       TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 500),
  description TEXT,

  -- State machine — 7 statuses (Paperclip parity)
  status TEXT NOT NULL DEFAULT 'backlog' CHECK (status IN
    ('backlog', 'todo', 'in_progress', 'in_review', 'blocked', 'done', 'cancelled')),
  priority TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN
    ('critical', 'high', 'medium', 'low')),

  -- Assignee — dual-field, application enforces XOR. Either or neither, never both.
  assignee_agent_id UUID REFERENCES public.ai_agents(id) ON DELETE SET NULL,
  assignee_user_id  UUID REFERENCES auth.users(id)        ON DELETE SET NULL,

  -- Creator (one of agent / user must be set; CHECK below)
  created_by_agent_id UUID REFERENCES public.ai_agents(id) ON DELETE SET NULL,
  created_by_user_id  UUID REFERENCES auth.users(id)        ON DELETE SET NULL,

  -- Execution (DBOS-managed lifecycle metadata)
  dbos_workflow_id     TEXT,                  -- handle returned by DBOS.start_workflow
  execution_locked_at  TIMESTAMPTZ,           -- atomic checkout marker
  execution_state      JSONB,                 -- agent intermediate state

  -- Origin classification (not in identifier)
  origin_kind TEXT NOT NULL DEFAULT 'manual' CHECK (origin_kind IN
    ('manual', 'chat_delegate', 'celery_pipeline', 'agent_dispatch', 'routine', 'escalation')),
  origin_id           TEXT,
  origin_fingerprint  TEXT NOT NULL DEFAULT 'default',

  -- Workforce delegation safety
  request_depth INTEGER NOT NULL DEFAULT 0 CHECK (request_depth >= 0 AND request_depth < 100),

  -- Billing (M2 wiring)
  billing_code TEXT,

  -- Lifecycle timestamps
  started_at    TIMESTAMPTZ,
  completed_at  TIMESTAMPTZ,
  cancelled_at  TIMESTAMPTZ,
  hidden_at     TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Application-level invariants
  CONSTRAINT issues_assignee_xor CHECK (
    NOT (assignee_agent_id IS NOT NULL AND assignee_user_id IS NOT NULL)
  ),
  CONSTRAINT issues_creator_required CHECK (
    created_by_agent_id IS NOT NULL OR created_by_user_id IS NOT NULL
  )
);

COMMENT ON TABLE public.issues IS
  'Top-level user-visible "thing". DBOS workflows reference issues.id via dbos_workflow_id and issues.dbos_workflow_id reciprocally. Schema ported from Paperclip (MIT) with mediahub adaptations.';

COMMENT ON COLUMN public.issues.dbos_workflow_id IS
  'DBOS workflow handle (string id from DBOS.start_workflow). NULL until execution starts.';
COMMENT ON COLUMN public.issues.request_depth IS
  'Delegation chain depth — incremented when an agent dispatches a child issue. Capped at 100 to break cycles.';


-- =============================================================================
-- Indexes (Paperclip schema/issues.ts:64-117 ported)
-- =============================================================================
CREATE UNIQUE INDEX issues_identifier_idx          ON public.issues(identifier);
CREATE INDEX issues_status_idx                     ON public.issues(status);
CREATE INDEX issues_assignee_agent_status_idx      ON public.issues(assignee_agent_id, status) WHERE assignee_agent_id IS NOT NULL;
CREATE INDEX issues_assignee_user_status_idx       ON public.issues(assignee_user_id,  status) WHERE assignee_user_id  IS NOT NULL;
CREATE INDEX issues_project_status_idx             ON public.issues(project_id, status);
CREATE INDEX issues_team_status_idx                ON public.issues(team_id,    status);
CREATE INDEX issues_parent_idx                     ON public.issues(parent_id);
CREATE INDEX issues_origin_idx                     ON public.issues(origin_kind, origin_id);
CREATE INDEX issues_created_by_user_idx            ON public.issues(created_by_user_id) WHERE created_by_user_id IS NOT NULL;
CREATE INDEX issues_dbos_workflow_idx              ON public.issues(dbos_workflow_id) WHERE dbos_workflow_id IS NOT NULL;
CREATE INDEX issues_created_at_idx                 ON public.issues(created_at DESC);

-- Idempotency for routine-origin issues — a routine can only have one open
-- issue per (origin_id, origin_fingerprint).
CREATE UNIQUE INDEX issues_open_routine_execution_uq
  ON public.issues(origin_kind, origin_id, origin_fingerprint)
  WHERE origin_kind = 'routine'
    AND status NOT IN ('done', 'cancelled')
    AND hidden_at IS NULL;

-- Full-text trigram search across identifier / title / description
CREATE INDEX issues_identifier_trgm_idx  ON public.issues USING gin (identifier  gin_trgm_ops);
CREATE INDEX issues_title_trgm_idx       ON public.issues USING gin (title       gin_trgm_ops);
CREATE INDEX issues_description_trgm_idx ON public.issues USING gin (description gin_trgm_ops) WHERE description IS NOT NULL;


-- =============================================================================
-- updated_at auto-touch trigger
-- =============================================================================
CREATE OR REPLACE FUNCTION public.touch_issue_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS issues_touch_updated_at ON public.issues;
CREATE TRIGGER issues_touch_updated_at
  BEFORE UPDATE ON public.issues
  FOR EACH ROW EXECUTE FUNCTION public.touch_issue_updated_at();


-- =============================================================================
-- issue_next_identifier — atomic counter RPC
-- =============================================================================
-- Caller pattern (DBOS workflow / FastAPI handler):
--   SELECT * FROM issue_next_identifier();   -- returns (issue_number, identifier)
--   INSERT INTO issues (issue_number, identifier, ...) VALUES (n, ident, ...);
--
-- Both the counter UPDATE and the INSERT must run in the same transaction —
-- if the INSERT fails, the counter rolls back too (unlike PG sequences).
--
-- SECURITY DEFINER lets app roles (mediahub_app, service_role) call without
-- needing direct UPDATE privilege on issue_sequence.
CREATE OR REPLACE FUNCTION public.issue_next_identifier()
RETURNS TABLE (issue_number INTEGER, identifier TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  next_n INTEGER;
  pfx    TEXT;
BEGIN
  UPDATE public.issue_sequence
  SET counter = counter + 1
  WHERE scope = 'global'
  RETURNING counter, prefix INTO next_n, pfx;

  IF next_n IS NULL THEN
    RAISE EXCEPTION 'issue_sequence row missing for scope=global';
  END IF;

  RETURN QUERY SELECT next_n, pfx || '-' || next_n::text;
END;
$$;

REVOKE ALL ON FUNCTION public.issue_next_identifier() FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION public.issue_next_identifier() TO authenticated, service_role, mediahub_app, mediahub_dbos;


-- =============================================================================
-- Grants — base table privileges (RLS still applies for non-bypass roles)
-- =============================================================================
GRANT SELECT, INSERT, UPDATE        ON public.issues          TO authenticated, service_role, mediahub_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.issues          TO mediahub_dbos;  -- DBOS workflows mutate freely
GRANT SELECT, UPDATE                ON public.issue_sequence  TO mediahub_dbos, service_role;


-- =============================================================================
-- RLS — 6 policies from design doc P10 (PoC #10 verified version)
-- =============================================================================
ALTER TABLE public.issues ENABLE ROW LEVEL SECURITY;

-- 1. SELECT — visible to (creator OR assignee_user OR team-member OR project-visible),
--    AND not hidden (unless creator). hidden_at is the soft-delete signal.
CREATE POLICY issues_select ON public.issues
  FOR SELECT
  USING (
    (
      created_by_user_id = auth.uid()
      OR assignee_user_id = auth.uid()
      OR (team_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.team_members tm
        WHERE tm.team_id = public.issues.team_id AND tm.user_id = auth.uid()
      ))
      OR (project_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.projects p
        WHERE p.id = public.issues.project_id
          AND (
            p.owner_id = auth.uid()
            OR p.visibility = 'public'
            OR (p.team_id IS NOT NULL AND EXISTS (
              SELECT 1 FROM public.team_members tm2
              WHERE tm2.team_id = p.team_id AND tm2.user_id = auth.uid()
            ))
          )
      ))
    )
    AND (
      hidden_at IS NULL
      OR created_by_user_id = auth.uid()
    )
  );

-- 2. INSERT — created_by_user_id must equal auth.uid() (handler self-create).
--    DBOS workflows that need to create on behalf of agent path use service_role.
CREATE POLICY issues_insert ON public.issues
  FOR INSERT
  WITH CHECK (
    created_by_user_id = auth.uid()
  );

-- 3. UPDATE non-status fields — creator OR current assignee.
--    Status changes governed by separate policy below.
CREATE POLICY issues_update_general ON public.issues
  FOR UPDATE
  USING (
    created_by_user_id = auth.uid()
    OR assignee_user_id = auth.uid()
  )
  WITH CHECK (
    created_by_user_id = auth.uid()
    OR assignee_user_id = auth.uid()
  );

-- 4. UPDATE status — creator can transition to 'cancelled' from any state;
--    other status transitions are DBOS workflow's job (service_role).
--    NOTE: PG RLS does not have column-level UPDATE policies; we enforce this
--    at the application layer (router validates allowed transitions).
--    The general UPDATE policy above already restricts WHO; this is a
--    documentation marker for the column-level rule.

-- 5. DELETE — no policy; users go through hidden_at soft-delete via UPDATE.
--    service_role bypasses RLS for hard delete (privacy compliance, etc.).
--    No CREATE POLICY here = implicit deny for authenticated.

-- 6. ALL for service_role / mediahub_dbos — both bypass RLS naturally.
--    service_role has BYPASSRLS attribute; mediahub_dbos uses explicit
--    SET ROLE service_role inside DBOS step for cross-RLS writes
--    (see PoC #11 for verified pattern).
