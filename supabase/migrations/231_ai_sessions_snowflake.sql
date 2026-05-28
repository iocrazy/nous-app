-- 231_ai_sessions_snowflake.sql
-- Convert ai_sessions.id from UUID to BIGINT Snowflake.
-- Mirrors mig 051's column-rename pattern. Rebuilds all FK dependents
-- in the same transaction.
--
-- FK dependents (verified by prod pg_constraint introspection 2026-05-28):
--   issues.ai_session_id          (mig 224, ON DELETE SET NULL)
--   ai_session_memory.session_id  (mig 187, ON DELETE CASCADE, PRIMARY KEY)
--   agent_runs.session_id         (mig 145, ON DELETE SET NULL)
--   ai_messages.session_id        (mig 121/138, ON DELETE CASCADE)
--
-- RLS policies on ai_messages and ai_session_memory reference ai_sessions.id
-- via subqueries. PostgreSQL treats these as dependents and blocks DROP COLUMN
-- unless the policies are dropped first (same pattern as mig 051).
--
-- Defensive IF EXISTS guards allow this migration to run safely even when
-- the local dev DB is behind on migrations (issues / ai_session_memory /
-- agent_runs may not exist yet in local snapshots). In production all four
-- tables must exist before applying this migration.
--
-- See docs/superpowers/specs/2026-05-28-id-unification-design.md § 4.

BEGIN;

-- ==========================================================================
-- SECTION 0: Drop RLS policies that depend on ai_sessions.id
-- Must happen before any DROP COLUMN on ai_sessions, otherwise PostgreSQL
-- refuses to drop the id column (policies reference it via subquery).
-- ==========================================================================

-- ai_messages policies (always present from mig 121/138)
DROP POLICY IF EXISTS ai_messages_read   ON public.ai_messages;
DROP POLICY IF EXISTS ai_messages_insert ON public.ai_messages;
DROP POLICY IF EXISTS ai_messages_delete ON public.ai_messages;

-- ai_session_memory policies (table may not exist in local dev DBs)
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'ai_session_memory'
    ) THEN
        DROP POLICY IF EXISTS "own_session_memory_readable"       ON public.ai_session_memory;
        DROP POLICY IF EXISTS "session_memory_write_service_only" ON public.ai_session_memory;
    END IF;
END $$;

-- ai_sessions policies themselves (reference user_id/team_id/project_id, not id;
-- drop anyway because the PK swap invalidates the table shape)
DROP POLICY IF EXISTS ai_sessions_read   ON public.ai_sessions;
DROP POLICY IF EXISTS ai_sessions_insert ON public.ai_sessions;
DROP POLICY IF EXISTS ai_sessions_update ON public.ai_sessions;
DROP POLICY IF EXISTS ai_sessions_delete ON public.ai_sessions;

-- ==========================================================================
-- SECTION 1: Add new BIGINT id column on ai_sessions
-- ==========================================================================

ALTER TABLE public.ai_sessions ADD COLUMN new_id BIGINT;
UPDATE public.ai_sessions SET new_id = generate_snowflake_id() WHERE new_id IS NULL;
ALTER TABLE public.ai_sessions ALTER COLUMN new_id SET NOT NULL;

-- ==========================================================================
-- SECTION 2: Mirror new FK columns on dependents + backfill
-- ==========================================================================

-- ai_messages is always present (mig 121)
ALTER TABLE public.ai_messages ADD COLUMN new_session_id BIGINT;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'issues'
    ) THEN
        ALTER TABLE public.issues ADD COLUMN new_ai_session_id BIGINT;
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'ai_session_memory'
    ) THEN
        ALTER TABLE public.ai_session_memory ADD COLUMN new_session_id BIGINT;
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'agent_runs'
    ) THEN
        ALTER TABLE public.agent_runs ADD COLUMN new_session_id BIGINT;
    END IF;
END $$;

-- Backfill new FK columns using the ai_sessions.new_id mapping
UPDATE public.ai_messages am
   SET new_session_id = s.new_id
  FROM public.ai_sessions s
 WHERE am.session_id = s.id;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'issues'
    ) THEN
        UPDATE public.issues i
           SET new_ai_session_id = s.new_id
          FROM public.ai_sessions s
         WHERE i.ai_session_id = s.id;
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'ai_session_memory'
    ) THEN
        UPDATE public.ai_session_memory m
           SET new_session_id = s.new_id
          FROM public.ai_sessions s
         WHERE m.session_id = s.id;
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'agent_runs'
    ) THEN
        UPDATE public.agent_runs r
           SET new_session_id = s.new_id
          FROM public.ai_sessions s
         WHERE r.session_id = s.id;
    END IF;
END $$;

-- ==========================================================================
-- SECTION 3: Drop old FK constraints
-- ==========================================================================

-- ai_messages always present
ALTER TABLE public.ai_messages DROP CONSTRAINT IF EXISTS ai_messages_session_id_fkey;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'issues'
    ) THEN
        ALTER TABLE public.issues DROP CONSTRAINT IF EXISTS issues_ai_session_id_fkey;
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'ai_session_memory'
    ) THEN
        ALTER TABLE public.ai_session_memory DROP CONSTRAINT IF EXISTS ai_session_memory_session_id_fkey;
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'agent_runs'
    ) THEN
        ALTER TABLE public.agent_runs DROP CONSTRAINT IF EXISTS agent_runs_session_id_fkey;
    END IF;
END $$;

-- ==========================================================================
-- SECTION 4: Drop old PK on ai_sessions + swap columns
-- ==========================================================================

ALTER TABLE public.ai_sessions DROP CONSTRAINT IF EXISTS ai_sessions_pkey;
ALTER TABLE public.ai_sessions DROP COLUMN id;
ALTER TABLE public.ai_sessions RENAME COLUMN new_id TO id;
ALTER TABLE public.ai_sessions ADD PRIMARY KEY (id);
ALTER TABLE public.ai_sessions ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- ==========================================================================
-- SECTION 5: Swap FK columns on dependents
-- ==========================================================================

-- ai_messages.session_id: nullable column (mig 121)
ALTER TABLE public.ai_messages DROP COLUMN session_id;
ALTER TABLE public.ai_messages RENAME COLUMN new_session_id TO session_id;

-- issues.ai_session_id: plain nullable column (mig 224)
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'issues'
    ) THEN
        ALTER TABLE public.issues DROP COLUMN ai_session_id;
        ALTER TABLE public.issues RENAME COLUMN new_ai_session_id TO ai_session_id;
    END IF;
END $$;

-- ai_session_memory.session_id was PRIMARY KEY (mig 187) — drop+recreate PK
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'ai_session_memory'
    ) THEN
        ALTER TABLE public.ai_session_memory DROP CONSTRAINT IF EXISTS ai_session_memory_pkey;
        ALTER TABLE public.ai_session_memory DROP COLUMN session_id;
        ALTER TABLE public.ai_session_memory RENAME COLUMN new_session_id TO session_id;
        ALTER TABLE public.ai_session_memory ALTER COLUMN session_id SET NOT NULL;
        ALTER TABLE public.ai_session_memory ADD PRIMARY KEY (session_id);
    END IF;
END $$;

-- agent_runs.session_id: nullable column (mig 145)
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'agent_runs'
    ) THEN
        ALTER TABLE public.agent_runs DROP COLUMN session_id;
        ALTER TABLE public.agent_runs RENAME COLUMN new_session_id TO session_id;
    END IF;
END $$;

-- ==========================================================================
-- SECTION 6: Rebuild FK constraints (preserve original ON DELETE behavior)
-- ==========================================================================

-- ai_messages always present
ALTER TABLE public.ai_messages
    ADD CONSTRAINT ai_messages_session_id_fkey
    FOREIGN KEY (session_id) REFERENCES public.ai_sessions(id) ON DELETE CASCADE;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'issues'
    ) THEN
        ALTER TABLE public.issues
            ADD CONSTRAINT issues_ai_session_id_fkey
            FOREIGN KEY (ai_session_id) REFERENCES public.ai_sessions(id) ON DELETE SET NULL;
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'ai_session_memory'
    ) THEN
        ALTER TABLE public.ai_session_memory
            ADD CONSTRAINT ai_session_memory_session_id_fkey
            FOREIGN KEY (session_id) REFERENCES public.ai_sessions(id) ON DELETE CASCADE;
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'agent_runs'
    ) THEN
        ALTER TABLE public.agent_runs
            ADD CONSTRAINT agent_runs_session_id_fkey
            FOREIGN KEY (session_id) REFERENCES public.ai_sessions(id) ON DELETE SET NULL;
    END IF;
END $$;

-- ==========================================================================
-- SECTION 7: Rebuild indexes on FK columns
-- ==========================================================================

-- idx_ai_messages_session: existed from mig 121/138 (session_id, created_at)
DROP INDEX IF EXISTS public.idx_ai_messages_session;
CREATE INDEX idx_ai_messages_session
    ON public.ai_messages(session_id, created_at);

-- idx_agent_runs_session: existed from mig 145 (partial WHERE session_id IS NOT NULL)
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'agent_runs'
    ) THEN
        DROP INDEX IF EXISTS public.idx_agent_runs_session;
        CREATE INDEX idx_agent_runs_session
            ON public.agent_runs(session_id) WHERE session_id IS NOT NULL;
    END IF;
END $$;

-- idx_issues_ai_session: mig 224 did not create one; add it now
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'issues'
    ) THEN
        CREATE INDEX IF NOT EXISTS idx_issues_ai_session
            ON public.issues(ai_session_id) WHERE ai_session_id IS NOT NULL;
    END IF;
END $$;

-- ==========================================================================
-- SECTION 8: Recreate RLS policies
-- ==========================================================================

-- ---- ai_sessions policies (from mig 138) ----
DROP POLICY IF EXISTS ai_sessions_read ON public.ai_sessions;
CREATE POLICY ai_sessions_read ON public.ai_sessions FOR SELECT
  USING (
    user_id = auth.uid()
    OR team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    OR project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid())
  );

DROP POLICY IF EXISTS ai_sessions_insert ON public.ai_sessions;
CREATE POLICY ai_sessions_insert ON public.ai_sessions FOR INSERT
  WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS ai_sessions_update ON public.ai_sessions;
CREATE POLICY ai_sessions_update ON public.ai_sessions FOR UPDATE
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS ai_sessions_delete ON public.ai_sessions;
CREATE POLICY ai_sessions_delete ON public.ai_sessions FOR DELETE
  USING (user_id = auth.uid());

-- ---- ai_messages policies (from mig 138) ----
DROP POLICY IF EXISTS ai_messages_read ON public.ai_messages;
CREATE POLICY ai_messages_read ON public.ai_messages FOR SELECT
  USING (
    session_id IN (
      SELECT id FROM public.ai_sessions
      WHERE user_id = auth.uid()
         OR team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
         OR project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid())
    )
  );

DROP POLICY IF EXISTS ai_messages_insert ON public.ai_messages;
CREATE POLICY ai_messages_insert ON public.ai_messages FOR INSERT
  WITH CHECK (
    session_id IN (SELECT id FROM public.ai_sessions WHERE user_id = auth.uid())
  );

DROP POLICY IF EXISTS ai_messages_delete ON public.ai_messages;
CREATE POLICY ai_messages_delete ON public.ai_messages FOR DELETE
  USING (
    session_id IN (SELECT id FROM public.ai_sessions WHERE user_id = auth.uid())
  );

-- ---- ai_session_memory policies (from mig 187) ----
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'ai_session_memory'
    ) THEN
        DROP POLICY IF EXISTS "own_session_memory_readable"       ON public.ai_session_memory;
        DROP POLICY IF EXISTS "session_memory_write_service_only" ON public.ai_session_memory;

        CREATE POLICY "own_session_memory_readable" ON public.ai_session_memory FOR SELECT
          USING (
            EXISTS (
              SELECT 1 FROM public.ai_sessions s
              WHERE s.id = ai_session_memory.session_id
                AND s.user_id = auth.uid()
            )
          );

        CREATE POLICY "session_memory_write_service_only" ON public.ai_session_memory
          FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- ==========================================================================
-- SECTION 9: PostgREST schema reload
-- ==========================================================================

NOTIFY pgrst, 'reload schema';

COMMIT;
