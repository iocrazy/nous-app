-- 505: clear agent orphans, then give agent_skills / conversation_ai_meta real
-- FKs to ai_agents; agent_runs parent/root FKs become ON DELETE SET NULL.
--
-- Why
-- ===
-- 501 made deleting an agent a soft delete and ticketed the rest: the two
-- tables that point at ai_agents without an FK kept dangling ids, and a hard
-- delete of a run still took other agents' Delegate children with it through
-- agent_runs.parent_run_id CASCADE.
--
-- Prod readings (read-only, 2026-09-25): ai_agents 20, agent_skills 10,
-- conversation_ai_meta 146, agent_runs 332.
--   * 4 orphan agent_skills rows (agents 578c2346… x3, 81e490db… x1, both
--     hard-deleted before 501). Unreachable bindings -> DELETE.
--   * 37 orphan conversation_ai_meta rows, all probe / e2e agents
--     (plain-text-probe 15, aline-qwen-probe 6, e2e-probe-agent 4, …);
--     18 had messages in the last 30 days and 2 issues point at them through
--     ai_session_id. None can be re-linked by slug.
--
-- Why meta orphans are NULLed, not deleted
-- ========================================
-- The row is the conversation's AI sidecar; deleting it strips 37 live
-- conversations (two of them issue sessions). Chat resolves by agent_slug
-- first and only uses agent_id as a tie-breaker / tombstone lookup, and
-- agent_deleted (409) keys on ai_agents.deleted_at, never on agent_id IS NULL.
-- Today these rows answer 404 "agent slug not found" because the bound id
-- resolves to nothing; with agent_id NULL they answer the same 404. No
-- user-visible change. Rebinding (issue_session) writes both columns, so a
-- NULL id heals on reassign. The new FK is SET NULL for the same reason.
--
-- Why agent_skills_agent_id_fkey is DROP IF EXISTS + ADD
-- ======================================================
-- The baseline (dumped from prod on 2026-07-15) and the ORM both carry it
-- (from 138), so the drift DB has it. Prod does not: it was lost after the
-- baseline dump and no repo migration drops it. Most likely a dump/restore
-- during the NAS -> gpupc move skipped this one ADD. That is how the 4 orphans
-- got in. A bare ADD would fail with 42710 on the drift DB; DROP IF EXISTS +
-- ADD converges both shapes. Every constraint below uses the same form, so a
-- second run is a no-op.
--
-- Why root_run_id goes SET NULL too
-- =================================
-- With parent SET NULL alone, deleting a root that still has descendants hits
-- 23503 through root_run_id (NO ACTION). Every reader treats a NULL root as
-- self-rooted: the tree key is COALESCE(root_run_id, id) in
-- agent_runs_repository (own_cost trees, run_ids_in_trees),
-- settle_tree_if_closed resolves "my root" as root_run_id or else itself, and
-- the cascade-cancel depth guard trigger uses COALESCE(NEW.root_run_id, NEW.id).
-- A survivor of a deleted root therefore becomes its own single-node tree, the
-- same shape as the already-documented "orphan child run" limitation in
-- tree_charge. Nothing in the app hard-deletes runs or agents (the API is soft
-- delete since 501), so these rules fire only on manual SQL or a future purge.
-- Caveat for that purge: a survivor ended within the last 7 days with no
-- billing stamp of its own can be nominated by the stale-tree sweeper and
-- charged as its own tree. Purge settled trees whole, not their roots alone.
--
-- Row counts are tiny; plain validating ADD CONSTRAINT is fine (no NOT VALID).
-- Order matters: orphans are cleared before the FKs that would reject them.
--
-- Not done here: project_stage_nodes.owner_agent_id and
-- generated_media.origin_run_id still have no FK.

DELETE FROM public.agent_skills s
 WHERE NOT EXISTS (SELECT 1 FROM public.ai_agents a WHERE a.id = s.agent_id);

UPDATE public.conversation_ai_meta m
   SET agent_id = NULL
 WHERE m.agent_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM public.ai_agents a WHERE a.id = m.agent_id);

ALTER TABLE public.agent_skills
    DROP CONSTRAINT IF EXISTS agent_skills_agent_id_fkey,
    ADD CONSTRAINT agent_skills_agent_id_fkey
        FOREIGN KEY (agent_id) REFERENCES public.ai_agents(id) ON DELETE CASCADE;

ALTER TABLE public.conversation_ai_meta
    DROP CONSTRAINT IF EXISTS conversation_ai_meta_agent_id_fkey,
    ADD CONSTRAINT conversation_ai_meta_agent_id_fkey
        FOREIGN KEY (agent_id) REFERENCES public.ai_agents(id) ON DELETE SET NULL;

ALTER TABLE public.agent_runs
    DROP CONSTRAINT IF EXISTS agent_runs_parent_run_id_fkey,
    ADD CONSTRAINT agent_runs_parent_run_id_fkey
        FOREIGN KEY (parent_run_id) REFERENCES public.agent_runs(id) ON DELETE SET NULL;

ALTER TABLE public.agent_runs
    DROP CONSTRAINT IF EXISTS agent_runs_root_run_id_fkey,
    ADD CONSTRAINT agent_runs_root_run_id_fkey
        FOREIGN KEY (root_run_id) REFERENCES public.agent_runs(id) ON DELETE SET NULL;
