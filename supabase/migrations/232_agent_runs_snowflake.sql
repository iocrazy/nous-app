-- 232_agent_runs_snowflake.sql
-- Convert agent_runs.id from UUID to BIGINT Snowflake.
-- Most complex of the three PR-B ID migrations: 7 FK dependents in 5 tables
-- including 2 self-FKs (parent_run_id, root_run_id) on agent_runs itself.
--
-- FK dependents (verified from migration source files 2026-05-28):
--   agent_run_events.run_id             (mig 155, ON DELETE CASCADE)
--   agent_memories.run_id               (mig 147, ON DELETE SET NULL)
--   agent_tasks.current_run_id          (mig 159, ON DELETE SET NULL)
--   agent_commitments.fulfillment_run_id (mig 186, ON DELETE SET NULL)
--   issue_messages.agent_run_id         (mig 205, ON DELETE SET NULL)
--   agent_runs.parent_run_id (self-FK)  (mig 159, ON DELETE CASCADE)
--   agent_runs.root_run_id  (self-FK)   (mig 159, ON DELETE RESTRICT / default)
--
-- RLS policies dropped before id-column operations:
--   agent_run_events: "own_events_readable" (subquery on agent_runs.id)
--   agent_runs own policies (own/team/project runs_readable + service_role_all)
--
-- Defensive IF EXISTS guards allow this migration to run safely even when
-- the local dev DB is behind on migrations (agent_runs and its 5 dependents
-- may not exist yet in local snapshots). In production all tables must exist.
--
-- Read-only window estimate: ~30-60 s depending on row counts.
--
-- See docs/superpowers/specs/2026-05-28-id-unification-design.md § 4.

BEGIN;

DO $main$ DECLARE
  _agent_runs_exists BOOLEAN;
BEGIN

SELECT EXISTS(
  SELECT 1 FROM information_schema.tables
   WHERE table_schema = 'public' AND table_name = 'agent_runs'
) INTO _agent_runs_exists;

IF NOT _agent_runs_exists THEN
  RAISE NOTICE 'agent_runs table not found — skipping mig 232 (local dev DB is behind)';
  -- Still emit NOTIFY so PostgREST doesn't stall on a cache miss.
  PERFORM pg_notify('pgrst', 'reload schema');
  RETURN;
END IF;

-- ========================================================================
-- SECTION 0: Drop RLS policies that reference agent_runs.id
-- Must happen before any DROP COLUMN on agent_runs.
-- ========================================================================

-- agent_run_events: "own_events_readable" subqueries agent_runs.id
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_run_events'
) THEN
    DROP POLICY IF EXISTS "own_events_readable" ON public.agent_run_events;
    DROP POLICY IF EXISTS "events_service_full" ON public.agent_run_events;
END IF;

-- agent_runs own policies (subquery themselves during PK swap)
DROP POLICY IF EXISTS "own_runs_readable"      ON public.agent_runs;
DROP POLICY IF EXISTS "team_runs_readable"     ON public.agent_runs;
DROP POLICY IF EXISTS "project_runs_readable"  ON public.agent_runs;
DROP POLICY IF EXISTS "service_role_all"       ON public.agent_runs;

-- ========================================================================
-- SECTION 1: Add new BIGINT id column on agent_runs
--            Add new self-FK columns on agent_runs
--            Add new FK columns on all 5 dependent tables
-- ========================================================================

ALTER TABLE public.agent_runs ADD COLUMN new_id BIGINT;
UPDATE public.agent_runs SET new_id = generate_snowflake_id() WHERE new_id IS NULL;
ALTER TABLE public.agent_runs ALTER COLUMN new_id SET NOT NULL;

-- Self-FK columns (backfilled in Section 2 via self-join)
ALTER TABLE public.agent_runs ADD COLUMN new_parent_run_id BIGINT;
ALTER TABLE public.agent_runs ADD COLUMN new_root_run_id BIGINT;

-- agent_run_events.run_id (mig 155)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_run_events'
) THEN
    ALTER TABLE public.agent_run_events ADD COLUMN new_run_id BIGINT;
END IF;

-- agent_memories.run_id (mig 147)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_memories'
) THEN
    ALTER TABLE public.agent_memories ADD COLUMN new_run_id BIGINT;
END IF;

-- agent_tasks.current_run_id (mig 159; merged into task_tracking via mig 200 but not dropped)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_tasks'
) THEN
    ALTER TABLE public.agent_tasks ADD COLUMN new_current_run_id BIGINT;
END IF;

-- agent_commitments.fulfillment_run_id (mig 186)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_commitments'
) THEN
    ALTER TABLE public.agent_commitments ADD COLUMN new_fulfillment_run_id BIGINT;
END IF;

-- issue_messages.agent_run_id (mig 205)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'issue_messages'
) THEN
    ALTER TABLE public.issue_messages ADD COLUMN new_agent_run_id BIGINT;
END IF;

-- ========================================================================
-- SECTION 2: Backfill new FK columns via join on agent_runs.new_id
-- ========================================================================

-- Self-FK: new_parent_run_id — join agent_runs to itself
UPDATE public.agent_runs r
   SET new_parent_run_id = parent.new_id
  FROM public.agent_runs parent
 WHERE r.parent_run_id = parent.id;

-- Self-FK: new_root_run_id — join agent_runs to itself
UPDATE public.agent_runs r
   SET new_root_run_id = root.new_id
  FROM public.agent_runs root
 WHERE r.root_run_id = root.id;

-- agent_run_events.run_id
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_run_events'
) THEN
    UPDATE public.agent_run_events e
       SET new_run_id = r.new_id
      FROM public.agent_runs r
     WHERE e.run_id = r.id;
END IF;

-- agent_memories.run_id
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_memories'
) THEN
    UPDATE public.agent_memories m
       SET new_run_id = r.new_id
      FROM public.agent_runs r
     WHERE m.run_id = r.id;
END IF;

-- agent_tasks.current_run_id
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_tasks'
) THEN
    UPDATE public.agent_tasks t
       SET new_current_run_id = r.new_id
      FROM public.agent_runs r
     WHERE t.current_run_id = r.id;
END IF;

-- agent_commitments.fulfillment_run_id
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_commitments'
) THEN
    UPDATE public.agent_commitments c
       SET new_fulfillment_run_id = r.new_id
      FROM public.agent_runs r
     WHERE c.fulfillment_run_id = r.id;
END IF;

-- issue_messages.agent_run_id
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'issue_messages'
) THEN
    UPDATE public.issue_messages im
       SET new_agent_run_id = r.new_id
      FROM public.agent_runs r
     WHERE im.agent_run_id = r.id;
END IF;

-- ========================================================================
-- SECTION 3: Drop old FK constraints
-- Self-FKs first (they reference agent_runs.id which we are about to drop)
-- ========================================================================

-- Self-FK constraints on agent_runs
ALTER TABLE public.agent_runs DROP CONSTRAINT IF EXISTS agent_runs_parent_run_id_fkey;
ALTER TABLE public.agent_runs DROP CONSTRAINT IF EXISTS agent_runs_root_run_id_fkey;

-- External FK constraints
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_run_events'
) THEN
    ALTER TABLE public.agent_run_events DROP CONSTRAINT IF EXISTS agent_run_events_run_id_fkey;
END IF;

IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_memories'
) THEN
    ALTER TABLE public.agent_memories DROP CONSTRAINT IF EXISTS agent_memories_run_id_fkey;
END IF;

IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_tasks'
) THEN
    ALTER TABLE public.agent_tasks DROP CONSTRAINT IF EXISTS agent_tasks_current_run_id_fkey;
END IF;

IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_commitments'
) THEN
    ALTER TABLE public.agent_commitments DROP CONSTRAINT IF EXISTS agent_commitments_fulfillment_run_id_fkey;
END IF;

IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'issue_messages'
) THEN
    ALTER TABLE public.issue_messages DROP CONSTRAINT IF EXISTS issue_messages_agent_run_id_fkey;
END IF;

-- ========================================================================
-- SECTION 4: Drop old PK on agent_runs + swap id column
-- Also swap the old self-FK columns (constraints already dropped above)
-- ========================================================================

ALTER TABLE public.agent_runs DROP CONSTRAINT IF EXISTS agent_runs_pkey;
ALTER TABLE public.agent_runs DROP COLUMN id;
ALTER TABLE public.agent_runs RENAME COLUMN new_id TO id;
ALTER TABLE public.agent_runs ADD PRIMARY KEY (id);
ALTER TABLE public.agent_runs ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- Swap old self-FK columns
ALTER TABLE public.agent_runs DROP COLUMN parent_run_id;
ALTER TABLE public.agent_runs RENAME COLUMN new_parent_run_id TO parent_run_id;

ALTER TABLE public.agent_runs DROP COLUMN root_run_id;
ALTER TABLE public.agent_runs RENAME COLUMN new_root_run_id TO root_run_id;

-- ========================================================================
-- SECTION 5: Swap FK columns on dependent tables
-- ========================================================================

-- agent_run_events.run_id: NOT NULL (CASCADE parent, always set)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_run_events'
) THEN
    ALTER TABLE public.agent_run_events DROP COLUMN run_id;
    ALTER TABLE public.agent_run_events RENAME COLUMN new_run_id TO run_id;
    ALTER TABLE public.agent_run_events ALTER COLUMN run_id SET NOT NULL;
END IF;

-- agent_memories.run_id: nullable (SET NULL on delete)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_memories'
) THEN
    ALTER TABLE public.agent_memories DROP COLUMN run_id;
    ALTER TABLE public.agent_memories RENAME COLUMN new_run_id TO run_id;
END IF;

-- agent_tasks.current_run_id: nullable (SET NULL on delete)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_tasks'
) THEN
    ALTER TABLE public.agent_tasks DROP COLUMN current_run_id;
    ALTER TABLE public.agent_tasks RENAME COLUMN new_current_run_id TO current_run_id;
END IF;

-- agent_commitments.fulfillment_run_id: nullable (SET NULL on delete)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_commitments'
) THEN
    ALTER TABLE public.agent_commitments DROP COLUMN fulfillment_run_id;
    ALTER TABLE public.agent_commitments RENAME COLUMN new_fulfillment_run_id TO fulfillment_run_id;
END IF;

-- issue_messages.agent_run_id: nullable (SET NULL on delete)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'issue_messages'
) THEN
    ALTER TABLE public.issue_messages DROP COLUMN agent_run_id;
    ALTER TABLE public.issue_messages RENAME COLUMN new_agent_run_id TO agent_run_id;
END IF;

-- ========================================================================
-- SECTION 6: Rebuild FK constraints (preserve original ON DELETE behavior)
-- ========================================================================

-- Self-FKs on agent_runs
-- parent_run_id: ON DELETE CASCADE (mig 159)
ALTER TABLE public.agent_runs
    ADD CONSTRAINT agent_runs_parent_run_id_fkey
    FOREIGN KEY (parent_run_id) REFERENCES public.agent_runs(id) ON DELETE CASCADE;

-- root_run_id: ON DELETE RESTRICT (default — no clause in mig 159)
ALTER TABLE public.agent_runs
    ADD CONSTRAINT agent_runs_root_run_id_fkey
    FOREIGN KEY (root_run_id) REFERENCES public.agent_runs(id);

-- agent_run_events.run_id: ON DELETE CASCADE (mig 155)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_run_events'
) THEN
    ALTER TABLE public.agent_run_events
        ADD CONSTRAINT agent_run_events_run_id_fkey
        FOREIGN KEY (run_id) REFERENCES public.agent_runs(id) ON DELETE CASCADE;
END IF;

-- agent_memories.run_id: ON DELETE SET NULL (mig 147)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_memories'
) THEN
    ALTER TABLE public.agent_memories
        ADD CONSTRAINT agent_memories_run_id_fkey
        FOREIGN KEY (run_id) REFERENCES public.agent_runs(id) ON DELETE SET NULL;
END IF;

-- agent_tasks.current_run_id: ON DELETE SET NULL (mig 159)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_tasks'
) THEN
    ALTER TABLE public.agent_tasks
        ADD CONSTRAINT agent_tasks_current_run_id_fkey
        FOREIGN KEY (current_run_id) REFERENCES public.agent_runs(id) ON DELETE SET NULL;
END IF;

-- agent_commitments.fulfillment_run_id: ON DELETE SET NULL (mig 186)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_commitments'
) THEN
    ALTER TABLE public.agent_commitments
        ADD CONSTRAINT agent_commitments_fulfillment_run_id_fkey
        FOREIGN KEY (fulfillment_run_id) REFERENCES public.agent_runs(id) ON DELETE SET NULL;
END IF;

-- issue_messages.agent_run_id: ON DELETE SET NULL (mig 205)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'issue_messages'
) THEN
    ALTER TABLE public.issue_messages
        ADD CONSTRAINT issue_messages_agent_run_id_fkey
        FOREIGN KEY (agent_run_id) REFERENCES public.agent_runs(id) ON DELETE SET NULL;
END IF;

-- ========================================================================
-- SECTION 7: Rebuild indexes on FK columns
-- ========================================================================

-- idx_agent_runs_root_tree (mig 159): (root_run_id, started_at) partial WHERE NOT NULL
DROP INDEX IF EXISTS public.idx_agent_runs_root_tree;
CREATE INDEX idx_agent_runs_root_tree
    ON public.agent_runs(root_run_id, started_at)
    WHERE root_run_id IS NOT NULL;

-- idx_agent_runs_parent (mig 159 / 212): partial WHERE NOT NULL
DROP INDEX IF EXISTS public.idx_agent_runs_parent;
CREATE INDEX idx_agent_runs_parent
    ON public.agent_runs(parent_run_id)
    WHERE parent_run_id IS NOT NULL;

-- idx_agent_run_events_run (mig 155): (run_id, iteration)
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_run_events'
) THEN
    DROP INDEX IF EXISTS public.idx_agent_run_events_run;
    CREATE INDEX idx_agent_run_events_run
        ON public.agent_run_events(run_id, iteration);
END IF;

-- idx_issue_messages_run (mig 205): partial WHERE NOT NULL
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'issue_messages'
) THEN
    DROP INDEX IF EXISTS public.idx_issue_messages_run;
    CREATE INDEX idx_issue_messages_run
        ON public.issue_messages(agent_run_id)
        WHERE agent_run_id IS NOT NULL;
END IF;

-- idx_agent_runs_issue (mig 205): rebuild so query planner re-plans with new types
DROP INDEX IF EXISTS public.idx_agent_runs_issue;
CREATE INDEX idx_agent_runs_issue
    ON public.agent_runs(issue_id)
    WHERE issue_id IS NOT NULL;

-- ========================================================================
-- SECTION 8: Recreate RLS policies
-- ========================================================================

-- ---- agent_runs own policies (from mig 145) ----
DROP POLICY IF EXISTS "own_runs_readable" ON public.agent_runs;
CREATE POLICY "own_runs_readable" ON public.agent_runs FOR SELECT
  USING (user_id = auth.uid());

DROP POLICY IF EXISTS "team_runs_readable" ON public.agent_runs;
CREATE POLICY "team_runs_readable" ON public.agent_runs FOR SELECT
  USING (
    team_id IS NOT NULL
    AND EXISTS (
      SELECT 1 FROM team_members tm
      WHERE tm.team_id = agent_runs.team_id
        AND tm.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "project_runs_readable" ON public.agent_runs;
CREATE POLICY "project_runs_readable" ON public.agent_runs FOR SELECT
  USING (
    project_id IS NOT NULL
    AND EXISTS (
      SELECT 1 FROM projects p
      LEFT JOIN team_members tm ON tm.team_id = p.team_id
      WHERE p.id = agent_runs.project_id
        AND (p.owner_id = auth.uid() OR tm.user_id = auth.uid())
    )
  );

DROP POLICY IF EXISTS "service_role_all" ON public.agent_runs;
CREATE POLICY "service_role_all" ON public.agent_runs FOR ALL TO service_role
  USING (true) WITH CHECK (true);

-- ---- agent_run_events policies (from mig 155) ----
IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'agent_run_events'
) THEN
    DROP POLICY IF EXISTS "events_service_full" ON public.agent_run_events;
    CREATE POLICY "events_service_full" ON public.agent_run_events FOR ALL TO service_role
      USING (true) WITH CHECK (true);

    DROP POLICY IF EXISTS "own_events_readable" ON public.agent_run_events;
    CREATE POLICY "own_events_readable" ON public.agent_run_events FOR SELECT
      USING (
        EXISTS (
          SELECT 1 FROM public.agent_runs r
          WHERE r.id = agent_run_events.run_id
            AND r.user_id = auth.uid()
        )
      );
END IF;

END $main$;

-- ========================================================================
-- SECTION 9: PostgREST schema reload
-- (outside DO block so it always fires, even in the skip-early path)
-- ========================================================================

NOTIFY pgrst, 'reload schema';

COMMIT;
