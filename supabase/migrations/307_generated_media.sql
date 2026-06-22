-- 306 — generated_media: Tier-1 store for AI-generated media (sub-plan 5).
--
-- High-churn, isolated from the hot `resources` table. Every generation lands
-- here cheaply with provenance as first-class columns; only KEPT/USED items get
-- promoted into `resources` (Plan 3). Backend writes via the direct engine
-- (BYPASSRLS); RLS is service-role-only so PostgREST never exposes it to
-- anon/authenticated (mirrors worker_registry / log-table hardening #541/#542).

CREATE TABLE IF NOT EXISTS public.generated_media (
    id                   BIGINT      PRIMARY KEY DEFAULT generate_snowflake_id(),
    scope_id             BIGINT      NOT NULL,           -- teams.id (owning scope)
    creator_id          UUID        NOT NULL,            -- the user
    media_kind           TEXT        NOT NULL,           -- 'image' | 'video'
    mime                 TEXT,
    file_path            TEXT        NOT NULL,
    file_size_bytes      BIGINT,
    origin_kind          TEXT        NOT NULL,           -- 'agent_run' | 'canvas_run'
    origin_run_id        TEXT,
    agent_id             UUID,
    canvas_id            BIGINT,
    node_id              TEXT,
    prompt               TEXT,
    model                TEXT,
    provider             TEXT,
    params               JSONB       NOT NULL DEFAULT '{}'::jsonb,
    cost_cents           NUMERIC,
    parent_resource_id   BIGINT,
    derivation_kind      TEXT,
    promoted_resource_id BIGINT,                          -- set on promote (Plan 3)
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_genmedia_scope_created
    ON public.generated_media (scope_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_genmedia_origin_run ON public.generated_media (origin_run_id);
CREATE INDEX IF NOT EXISTS idx_genmedia_agent      ON public.generated_media (agent_id);
CREATE INDEX IF NOT EXISTS idx_genmedia_canvas     ON public.generated_media (canvas_id);
CREATE INDEX IF NOT EXISTS idx_genmedia_promoted   ON public.generated_media (promoted_resource_id);

ALTER TABLE public.generated_media ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS generated_media_service_role_all ON public.generated_media;
CREATE POLICY generated_media_service_role_all ON public.generated_media
    FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE public.generated_media IS
    'Tier-1 store for AI-generated media (sub-plan 5). Cheap/high-churn; promote '
    'to resources on keep/use. Backend-only (service-role RLS).';