-- supabase/migrations/325_agent_memory_promotions.sql
-- Agent Memory Phase C1 — promotion review queue. A pending proposal to flip a
-- private team/project memory row to shared. Admin-reviewed; nothing here auto-shares.

CREATE TABLE IF NOT EXISTS public.agent_memory_promotions (
    id                  BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    memory_id           BIGINT NOT NULL REFERENCES public.agent_memory(id) ON DELETE CASCADE,
    proposed_scope      TEXT NOT NULL CHECK (proposed_scope IN ('team','project')),
    target_team_id      BIGINT NOT NULL,            -- always set (project's team backfilled)
    target_project_id   BIGINT,                     -- set for project scope only
    classification_kind TEXT NOT NULL DEFAULT 'fact',
    confidence          REAL NOT NULL DEFAULT 0,
    justification       TEXT NOT NULL DEFAULT '',
    scrubbed_body_md    TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','approved','rejected')),
    reviewed_by         UUID,
    reviewed_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one OPEN proposal per memory row.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_memory_promotions_pending
    ON public.agent_memory_promotions (memory_id) WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_agent_memory_promotions_status
    ON public.agent_memory_promotions (status, created_at DESC);

ALTER TABLE public.agent_memory_promotions ENABLE ROW LEVEL SECURITY;

-- Service-role only — admin endpoints run as service role, like agent_memory writes.
DROP POLICY IF EXISTS agent_memory_promotions_service_only ON public.agent_memory_promotions;
CREATE POLICY agent_memory_promotions_service_only ON public.agent_memory_promotions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
