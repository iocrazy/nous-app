-- 404: agent_runs.episode_id — episode granularity for the A2 agent-run scope.
--
-- A2 (mig-less, dcd3b62) bound an agent run to a project via the pre-existing
-- agent_runs.project_id / team_id columns, and threaded an `episode_id` field
-- through AgentRunScope + scope_resolver's checks — but left it permanently
-- None because no column existed to stamp it into. B1 (402) then gave
-- project_stage_nodes an episode_id, and script_projects already carried one,
-- so "which episode is this run working in" became answerable at dispatch
-- time. This column is where that answer lands.
--
-- Same immutability contract as project_id / team_id (see
-- app/services/ai/scope/agent_run_scope.py): stamped exactly ONCE by
-- RunRecorder at insert, never updated afterward. Nothing in
-- AgentRunsRepository writes it post-insert, and
-- tests/test_agent_run_scope.py::test_no_repository_write_path_touches_scope_columns
-- keeps it that way.
--
-- NULL means "no episode-level restriction" — the run is scoped to the whole
-- project. That is the correct default for every pre-existing row and for
-- every dispatch path that cannot name an episode, and it is what makes this
-- migration behaviour-neutral on its own: scope_resolver only applies the
-- episode comparison when scope.episode_id IS NOT NULL.
--
-- ON DELETE SET NULL (matching 402's choice on project_stage_nodes.episode_id,
-- and issue_id/conversation_id on this same table): an agent_runs row is
-- immutable telemetry + cost history. Deleting an episode must not delete the
-- billing record of work done inside it; widening a FINISHED run's recorded
-- scope has no authorization consequence because scope_for_run is only ever
-- consulted for a live run's tool calls.

ALTER TABLE public.agent_runs
    ADD COLUMN IF NOT EXISTS episode_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agent_runs_episode_id_fkey'
    ) THEN
        ALTER TABLE public.agent_runs
            ADD CONSTRAINT agent_runs_episode_id_fkey
            FOREIGN KEY (episode_id) REFERENCES public.episodes(id)
            ON DELETE SET NULL;
    END IF;
END $$;

-- Partial index: the overwhelming majority of runs carry no episode, so the
-- index only needs to cover the ones that do (same shape as
-- idx_agent_runs_issue / idx_agent_runs_parent on this table).
CREATE INDEX IF NOT EXISTS idx_agent_runs_episode
    ON public.agent_runs (episode_id)
    WHERE episode_id IS NOT NULL;

COMMENT ON COLUMN public.agent_runs.episode_id IS
    'Server-bound episode scope for this run (A4). Stamped once at insert by '
    'RunRecorder, never updated. NULL = project-wide scope. Read by '
    'app/services/ai/scope/agent_run_scope.py::scope_for_run.';
