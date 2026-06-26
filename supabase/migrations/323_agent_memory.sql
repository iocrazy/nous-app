-- supabase/migrations/323_agent_memory.sql
-- Agent Memory Layer Phase A — curated, scoped agent memory + ranked FTS recall.
-- Read-path only; no writers in Phase A. The codebase's first tsvector/GIN-tsvector.

CREATE TABLE IF NOT EXISTS public.agent_memory (
    id                  BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    scope               TEXT NOT NULL CHECK (scope IN ('session','user','agent_user','project','team')),
    owner_user_id       UUID NOT NULL,
    team_id             BIGINT,
    project_id          BIGINT,
    agent_id            UUID,
    session_id          BIGINT,
    visibility          TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','shared')),
    kind                TEXT NOT NULL DEFAULT 'fact' CHECK (kind IN ('fact','decision','preference','procedure')),
    title               TEXT NOT NULL DEFAULT '',
    body_md             TEXT NOT NULL DEFAULT '',
    when_to_use         TEXT NOT NULL DEFAULT '',
    fingerprint         TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','superseded')),
    reinforcement_count INT NOT NULL DEFAULT 0,
    last_recalled_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Ranked recall index: weight title+when_to_use (A) above body (B).
    search_tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '') || ' ' || coalesce(when_to_use, '')), 'A')
        || setweight(to_tsvector('english', coalesce(body_md, '')), 'B')
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_search ON public.agent_memory USING gin (search_tsv);
CREATE INDEX IF NOT EXISTS idx_agent_memory_owner ON public.agent_memory (owner_user_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_memory_team_shared ON public.agent_memory (team_id, visibility) WHERE visibility = 'shared';
CREATE INDEX IF NOT EXISTS idx_agent_memory_agent ON public.agent_memory (agent_id) WHERE agent_id IS NOT NULL;

ALTER TABLE public.agent_memory ENABLE ROW LEVEL SECURITY;

-- Read: own rows (any visibility) OR shared rows of a team the caller belongs to.
-- Project-shared rows also carry their project's team_id, so the team check covers them.
DROP POLICY IF EXISTS agent_memory_readable ON public.agent_memory;
CREATE POLICY agent_memory_readable ON public.agent_memory FOR SELECT
    USING (
        owner_user_id = (SELECT auth.uid())
        OR (visibility = 'shared'
            AND team_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))))
    );

-- Writes are service-role only (Phase B+ consolidation runs as service role).
DROP POLICY IF EXISTS agent_memory_write_service_only ON public.agent_memory;
CREATE POLICY agent_memory_write_service_only ON public.agent_memory FOR ALL
    TO service_role USING (true) WITH CHECK (true);
