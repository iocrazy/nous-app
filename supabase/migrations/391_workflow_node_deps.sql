-- 391_workflow_node_deps.sql — Project Workflow M3 (PR-J, W3 dependency edges).
--
-- Light DAG: a node may declare "depends_on" other nodes in the SAME template
-- (or, on the instance side, the same project). The core rule — a dependency
-- may only point at a node with a strictly smaller sort_order — is enforced
-- in the application layer (workflow_templates_repository / project_stage
-- _nodes_repository), never here; the DB side is just the edge storage plus
-- CASCADE cleanup so a deleted node's edges vanish with it (no dangling rows
-- for a since-removed node to leak through list_nodes / get_template).
--
-- Two tables, one per layer, mirroring the node tables themselves:
--   workflow_template_node_deps  — template-layer edges (mig 380 tables)
--   project_stage_node_deps      — instance-layer edges, copied at
--                                  instantiation (project_stage_nodes_
--                                  repository.instantiate_from_template)
--
-- Composite PK (node_id, depends_on_node_id) — both columns NOT NULL, so no
-- surrogate id is needed (unlike workflow_template_node_members /
-- project_stage_node_members, whose nullable user/agent XOR pair cannot form
-- a composite PK; see mig 380's note). Same idiom as canvas_resource_refs.
--
-- RLS: service-role-only lockdown, same as every other workflow table (mig
-- 380) — ownership is enforced in router guards (resolve_effective_role),
-- not RLS; the frontend never reaches these tables directly.

BEGIN;

CREATE TABLE IF NOT EXISTS public.workflow_template_node_deps (
    node_id            BIGINT NOT NULL
                       REFERENCES public.workflow_template_nodes(id) ON DELETE CASCADE,
    depends_on_node_id BIGINT NOT NULL
                       REFERENCES public.workflow_template_nodes(id) ON DELETE CASCADE,
    PRIMARY KEY (node_id, depends_on_node_id)
);

CREATE TABLE IF NOT EXISTS public.project_stage_node_deps (
    node_id            BIGINT NOT NULL
                       REFERENCES public.project_stage_nodes(id) ON DELETE CASCADE,
    depends_on_node_id BIGINT NOT NULL
                       REFERENCES public.project_stage_nodes(id) ON DELETE CASCADE,
    PRIMARY KEY (node_id, depends_on_node_id)
);

-- ── RLS: service-role-only lockdown on both edge tables (mig 380 idiom) ─────

ALTER TABLE public.workflow_template_node_deps ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on workflow_template_node_deps"
    ON public.workflow_template_node_deps;
CREATE POLICY "Service role full access on workflow_template_node_deps"
    ON public.workflow_template_node_deps FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

ALTER TABLE public.project_stage_node_deps ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on project_stage_node_deps"
    ON public.project_stage_node_deps;
CREATE POLICY "Service role full access on project_stage_node_deps"
    ON public.project_stage_node_deps FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

NOTIFY pgrst, 'reload schema';

COMMIT;
