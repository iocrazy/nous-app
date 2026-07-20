-- 380_workflow_nodes_m1.sql — Project Workflow M1 (PR-A).
--
-- Three-layer node model, single direction:
--   node bank (project_stages, upgraded)  →  team template
--   (workflow_templates + _nodes + _node_members)  →  per-project instance
--   (project_stage_nodes + _node_members).
-- Instances/hooks/advance land in PR-B; this migration only lays the schema.
--
-- Node-bank upgrade: project_stages gains phase / role / deliverable / review
-- columns so the 11-node catalog (seeded in 381) is a read-only ops dictionary.
--
-- RLS: every workflow table is backend-only. All access goes through FastAPI
-- with the service-role client (WorkflowTemplatesRepository etc.); the frontend
-- reaches them via /api/v1/workflows REST routes, never supabase-js. So the
-- service-role-only lockdown (375_gallery_items / 377_beat_templates) is the
-- correct fix — ownership is enforced in router guards, not RLS. project_stages
-- keeps its existing authenticated-read policy (unchanged here).
--
-- agent ids are UUID throughout (aligns ai_agents.id / issues.assignee_agent_id).
-- owner is single (user XOR agent) via CHECK; members are a multi-row side table.
-- The member tables carry a surrogate snowflake `id` PK — the spec DDL omitted
-- it, but a null-able XOR pair cannot form a composite PK, and the schema-drift
-- gate maps every domain table as a declarative model (which needs a PK). This
-- is the one deviation from the plan DDL, noted deliberately.

BEGIN;

-- ── 1. project_stages → global node bank ────────────────────────────────────
ALTER TABLE public.project_stages
    ADD COLUMN IF NOT EXISTS phase              TEXT,
    ADD COLUMN IF NOT EXISTS default_role_label TEXT,
    ADD COLUMN IF NOT EXISTS deliverable_label  TEXT,
    ADD COLUMN IF NOT EXISTS review_required    BOOLEAN NOT NULL DEFAULT false;

-- ── 2. workflow_templates (team-level) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.workflow_templates (
    id         BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    team_id    BIGINT NOT NULL,
    name       TEXT NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT false,
    created_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- At most one default template per team.
CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_templates_team_default
    ON public.workflow_templates (team_id) WHERE is_default;
CREATE INDEX IF NOT EXISTS idx_workflow_templates_team
    ON public.workflow_templates (team_id);

-- ── 3. workflow_template_nodes ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.workflow_template_nodes (
    id                     BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    template_id            BIGINT NOT NULL
                           REFERENCES public.workflow_templates(id) ON DELETE CASCADE,
    name                   TEXT NOT NULL,
    sort_order             INT NOT NULL,
    parallel_group         INT,
    default_owner_user_id  UUID,
    default_owner_agent_id UUID,
    skip_default           BOOLEAN NOT NULL DEFAULT false,
    review_required        BOOLEAN NOT NULL DEFAULT false,
    deliverable_required   BOOLEAN NOT NULL DEFAULT false,
    deliverable_label      TEXT,
    source_stage_id        BIGINT,
    duration_days          INT,
    CONSTRAINT wtn_owner_xor CHECK (
        NOT (default_owner_user_id IS NOT NULL AND default_owner_agent_id IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_wtn_template
    ON public.workflow_template_nodes (template_id, sort_order);

-- ── 4. workflow_template_node_members ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.workflow_template_node_members (
    id       BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    node_id  BIGINT NOT NULL
             REFERENCES public.workflow_template_nodes(id) ON DELETE CASCADE,
    user_id  UUID,
    agent_id UUID,
    CONSTRAINT wtnm_xor CHECK ((user_id IS NULL) <> (agent_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_wtnm_node_user
    ON public.workflow_template_node_members (node_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_wtnm_node_agent
    ON public.workflow_template_node_members (node_id, agent_id) WHERE agent_id IS NOT NULL;

-- ── 5. project_stage_nodes (per-project instance) ───────────────────────────
CREATE TABLE IF NOT EXISTS public.project_stage_nodes (
    id                      BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id              BIGINT NOT NULL
                            REFERENCES public.projects(id) ON DELETE CASCADE,
    source_template_node_id BIGINT,
    legacy_stage_id         BIGINT,
    name                    TEXT NOT NULL,
    sort_order              INT NOT NULL,
    parallel_group          INT,
    status                  TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','in_progress','in_review','done','skipped')),
    owner_user_id           UUID,
    owner_agent_id          UUID,
    planned_start           DATE,
    planned_due             DATE,
    review_required         BOOLEAN NOT NULL DEFAULT false,
    deliverable_required    BOOLEAN NOT NULL DEFAULT false,
    deliverable_label       TEXT,
    skipped                 BOOLEAN NOT NULL DEFAULT false,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT psn_owner_xor CHECK (
        NOT (owner_user_id IS NOT NULL AND owner_agent_id IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS psn_project_idx
    ON public.project_stage_nodes (project_id, sort_order);

-- ── 6. project_stage_node_members ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.project_stage_node_members (
    id       BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    node_id  BIGINT NOT NULL
             REFERENCES public.project_stage_nodes(id) ON DELETE CASCADE,
    user_id  UUID,
    agent_id UUID,
    CONSTRAINT psnm_xor CHECK ((user_id IS NULL) <> (agent_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_psnm_node_user
    ON public.project_stage_node_members (node_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_psnm_node_agent
    ON public.project_stage_node_members (node_id, agent_id) WHERE agent_id IS NOT NULL;

-- ── 7. issues.due_date (mirror issue inherits node planned_due in PR-B) ──────
ALTER TABLE public.issues ADD COLUMN IF NOT EXISTS due_date DATE;

-- ── 8. projects.current_node_id (workflow cursor; current_stage_id retired M2) ─
ALTER TABLE public.projects ADD COLUMN IF NOT EXISTS current_node_id BIGINT;

-- ── RLS: service-role-only lockdown on the new tables ───────────────────────
ALTER TABLE public.workflow_templates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on workflow_templates"
    ON public.workflow_templates;
CREATE POLICY "Service role full access on workflow_templates"
    ON public.workflow_templates FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

ALTER TABLE public.workflow_template_nodes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on workflow_template_nodes"
    ON public.workflow_template_nodes;
CREATE POLICY "Service role full access on workflow_template_nodes"
    ON public.workflow_template_nodes FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

ALTER TABLE public.workflow_template_node_members ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on workflow_template_node_members"
    ON public.workflow_template_node_members;
CREATE POLICY "Service role full access on workflow_template_node_members"
    ON public.workflow_template_node_members FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

ALTER TABLE public.project_stage_nodes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on project_stage_nodes"
    ON public.project_stage_nodes;
CREATE POLICY "Service role full access on project_stage_nodes"
    ON public.project_stage_nodes FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

ALTER TABLE public.project_stage_node_members ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on project_stage_node_members"
    ON public.project_stage_node_members;
CREATE POLICY "Service role full access on project_stage_node_members"
    ON public.project_stage_node_members FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

NOTIFY pgrst, 'reload schema';

COMMIT;
