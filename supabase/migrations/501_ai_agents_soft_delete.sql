-- 501: ai_agents.deleted_at — deleting an agent becomes a soft delete.
--
-- Why
-- ===
-- DELETE /ai-library/agents/{slug} used to hard-delete the row. Eleven FKs
-- to ai_agents are ON DELETE CASCADE (agent_runs among them), and
-- agent_runs.parent_run_id is CASCADE too, so one delete removed:
--   * the agent's own runs, transcripts, deliverables and script_shot_ops —
--     issue/project cost rollups shrank after the fact;
--   * Delegate child runs owned by OTHER agents (second hop through
--     parent_run_id; prod 2026-09-23: 34 runs have a parent/root of a
--     different agent);
--   * issue_pipeline_steps rows (pipeline definitions silently lost steps).
-- Tables with no FK at all (conversation_ai_meta, agent_skills,
-- project_stage_nodes.owner_agent_id, generated_media.origin_run_id …) kept
-- dangling ids. Prod already carries 37 orphan conversation_ai_meta rows and
-- 4 orphan agent_skills from earlier deletes.
--
-- With deleted_at the row stays, so every history reader (runs, transcripts,
-- cost ledger, lineage, conversation members) keeps resolving. Selection
-- surfaces (agent lists, Delegate targets, bounds inventory, new chats) filter
-- deleted_at IS NULL in the repository layer. The API refuses the delete with
-- 409 agent_in_use while live routing still points at the agent.
--
-- Slug uniqueness
-- ===============
-- Checked with \d ai_agents on the drift DB and on prod (identical):
--   ux_ai_agents_slug_system UNIQUE (slug)          WHERE is_system_preset = true
--   ux_ai_agents_slug_user   UNIQUE (user_id, slug) WHERE is_system_preset = false
--                                                    AND user_id IS NOT NULL
-- Presets cannot be deleted (router 403 + repository guard), so the system
-- index is untouched. The user index gains AND deleted_at IS NULL: without it
-- a soft-deleted row would hold its slug forever, and "delete my agent, then
-- create one with the same name" would hit a unique violation (a 500) instead
-- of the create path's own friendly slug check. Prod has no (user_id, slug)
-- duplicates today, so the rebuilt index cannot fail. The table is ~20 rows;
-- a plain (non-CONCURRENT) rebuild is fine inside the migration batch.
--
-- Not done here (ticketed): FKs for agent_skills / conversation_ai_meta need
-- the orphan cleanup first; agent_runs.parent_run_id CASCADE -> SET NULL.

ALTER TABLE public.ai_agents
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

COMMENT ON COLUMN public.ai_agents.deleted_at IS
    'Soft delete. Non-NULL = deleted: hidden from selection surfaces, kept for history (runs, transcripts, costs).';

DROP INDEX IF EXISTS public.ux_ai_agents_slug_user;
CREATE UNIQUE INDEX ux_ai_agents_slug_user
    ON public.ai_agents USING btree (user_id, slug)
    WHERE is_system_preset = false AND user_id IS NOT NULL AND deleted_at IS NULL;
