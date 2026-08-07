-- 409: projects.workflow_template_id + workflow_method — remember a project's
--       workflow binding so B3 can (a) fan a template out into a per-episode
--       node chain at attach/create, and (b) auto-instantiate a chain for any
--       episode added LATER, reusing the same template + method.
--
-- Background: until B3, a project had no stored template binding at all.
-- projects.workflow_id (mig 047) points at the unrelated legacy
-- project_workflows table and is never written by the M1 template system; the
-- only "does this project have a workflow" signal was instance-node existence,
-- and the only path back to the template was each node's
-- source_template_node_id. That is enough to answer "attach once, fan out to
-- the episodes that exist right now", but NOT "a new episode arrived — which
-- template + method should its chain come from" (an empty project attached
-- before any episode has zero nodes to reverse-lookup). These two columns are
-- where that answer lands.
--
-- Both nullable, both NULL for a project with no workflow. Behaviour-neutral
-- on their own: no read path consults them until B3 wires instantiation.
--
-- workflow_template_id — FK → workflow_templates(id) ON DELETE SET NULL,
-- matching the soft-pointer discipline already used across this schema
-- (agent_runs.episode_id in 404, project_stage_nodes.episode_id in 402). A
-- deleted template demotes the project to "no auto-chain for future episodes"
-- rather than cascading into the project row; existing per-episode chains are
-- untouched (they are real project_stage_nodes, not derived from this column).
--
-- workflow_method — 'live' / 'ai' / 'hybrid', the same enum
-- instantiate_from_template already accepts. CHECK allows NULL (no workflow)
-- or one of the three, mirroring 402's surface CHECK idiom (NULL is a real
-- value, not "missing config").

ALTER TABLE public.projects
    ADD COLUMN IF NOT EXISTS workflow_template_id BIGINT;

ALTER TABLE public.projects
    ADD COLUMN IF NOT EXISTS workflow_method TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'projects_workflow_template_id_fkey'
    ) THEN
        ALTER TABLE public.projects
            ADD CONSTRAINT projects_workflow_template_id_fkey
            FOREIGN KEY (workflow_template_id)
            REFERENCES public.workflow_templates(id)
            ON DELETE SET NULL;
    END IF;
END $$;

ALTER TABLE public.projects
    DROP CONSTRAINT IF EXISTS projects_workflow_method_check;
ALTER TABLE public.projects
    ADD CONSTRAINT projects_workflow_method_check
    CHECK (
        workflow_method IS NULL
        OR workflow_method = ANY (ARRAY['live'::text, 'ai'::text, 'hybrid'::text])
    );

-- Partial index: the overwhelming majority of projects carry no workflow
-- template binding, so the index only needs to cover the ones that do (same
-- shape as idx_agent_runs_episode in 404).
CREATE INDEX IF NOT EXISTS idx_projects_workflow_template
    ON public.projects (workflow_template_id)
    WHERE workflow_template_id IS NOT NULL;

COMMENT ON COLUMN public.projects.workflow_template_id IS
    'B3 workflow binding: the workflow_templates.id this project was '
    'instantiated from. Written at attach/create; read to instantiate a chain '
    'for a newly added episode. NULL = no workflow. ON DELETE SET NULL.';
COMMENT ON COLUMN public.projects.workflow_method IS
    'B3 workflow binding: instantiation method (live/ai/hybrid) frozen at '
    'attach/create so every episode chain in this project is built the same '
    'way. NULL = no workflow.';

NOTIFY pgrst, 'reload schema';
