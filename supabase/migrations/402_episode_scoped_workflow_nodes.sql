-- Migration 402: episode-scoped workflow nodes -- data layer only (B1)
--
-- Ledger: docs/superpowers/plans/2026-08-04-episode-workflow-and-agent-layer.md (B1)
-- Design: docs/superpowers/specs/2026-08-04-episode-level-workflow-design.md SS4/SS7
-- P0 audit: docs/superpowers/plans/2026-08-04-p0-cursor-audit.md SS4 (surface
--           copy-freeze), SS5.1/SS5.5 (why the cursor FK and the episode_id
--           index are shaped this way)
--
-- Three additions, landed together because they are mechanically independent
-- but conceptually one change -- B1's schema for B2 (advance_service rewire)
-- and B3 (per-episode instantiation) to build on:
--
-- (a) project_stage_nodes.episode_id -- nullable FK to episodes. NULL means a
--     legacy project-level node (every node in the DB today, plus anything
--     created before B3 wires per-episode instantiation). ON DELETE SET NULL,
--     not CASCADE: deleting an episode should demote its nodes to "legacy"
--     (hidden from UI per B5, not touched otherwise), not blow away the whole
--     node bank (mirror issues, folders, deps) as a bare FK side effect the
--     application layer never decided on. B6 is where genuinely orphaned
--     legacy nodes get cleaned up, deliberately, in code.
--
-- (b) surface TEXT on BOTH workflow_template_nodes and project_stage_nodes.
--     Declares which creation surface (script/storyboard/renders) a node
--     corresponds to; NULL = deliverable-type node (no in-app surface,
--     completed via upload+review). Copied verbatim template->instance at
--     instantiation and frozen there -- the exact same idiom as
--     completion_policy/events/form_schema (see
--     project_stage_nodes_repository.py::instantiate_from_template, and the
--     update_node keyword whitelist that already omits all three). UNLIKE
--     events (NOT NULL + server_default), surface is nullable with NO
--     default: NULL is a real, meaningful value (deliverable node), not
--     "missing config" -- the CHECK below allows NULL explicitly rather than
--     leaning on three-valued-logic to let it slide through unstated.
--
-- (c) episodes.current_node_id -- the per-episode workflow cursor the design
--     doc (SS4) and P0 report (SS5.5/SS6) both point at, replacing the
--     project's single projects.current_node_id (mig 380) for anything B2
--     rewires. projects.current_node_id is UNTOUCHED here and keeps being
--     the only cursor advance_service reads until B2 lands -- this migration
--     only lands the column and its accessor
--     (episode_repository.EpisodeRepository.set_current_node_id), it does
--     not change any read path. FK is ON DELETE SET NULL, fixing the gap mig
--     380 left on projects.current_node_id (no FK there at all, so a deleted
--     node left a dangling cursor id that advance_service._active_index
--     silently read as "nothing found -> group 0", per P0 SS5.5) rather than
--     repeating that gap on the new column.
--
-- Idempotent: every ADD COLUMN is IF NOT EXISTS; every CHECK constraint is an
-- unconditional DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT (same idiom as
-- migration 398), safe to rerun.

-- (a) episode_id on project_stage_nodes
ALTER TABLE public.project_stage_nodes
  ADD COLUMN IF NOT EXISTS episode_id BIGINT
    REFERENCES public.episodes(id) ON DELETE SET NULL;

-- Composite, not a bare episode_id index: every real query already has
-- project_id in hand (a node is always reached through its project), and this
-- column order serves both the existing "all nodes in a project" query
-- (leading column alone) and B2's future "all nodes in THIS episode of THIS
-- project" query (first two columns as equality, sort_order for the existing
-- ORDER BY) without needing a second index.
CREATE INDEX IF NOT EXISTS idx_project_stage_nodes_episode
  ON public.project_stage_nodes (project_id, episode_id, sort_order);

-- (b) surface on both tables -- nullable, no default (NULL is meaningful, see
-- header note above).
ALTER TABLE public.workflow_template_nodes ADD COLUMN IF NOT EXISTS surface TEXT;
ALTER TABLE public.project_stage_nodes ADD COLUMN IF NOT EXISTS surface TEXT;

ALTER TABLE public.workflow_template_nodes
  DROP CONSTRAINT IF EXISTS workflow_template_nodes_surface_check;
ALTER TABLE public.workflow_template_nodes
  ADD CONSTRAINT workflow_template_nodes_surface_check
  CHECK (
    surface IS NULL
    OR surface = ANY (ARRAY['script'::text, 'storyboard'::text, 'renders'::text])
  );

ALTER TABLE public.project_stage_nodes
  DROP CONSTRAINT IF EXISTS project_stage_nodes_surface_check;
ALTER TABLE public.project_stage_nodes
  ADD CONSTRAINT project_stage_nodes_surface_check
  CHECK (
    surface IS NULL
    OR surface = ANY (ARRAY['script'::text, 'storyboard'::text, 'renders'::text])
  );

-- (c) per-episode cursor
ALTER TABLE public.episodes
  ADD COLUMN IF NOT EXISTS current_node_id BIGINT
    REFERENCES public.project_stage_nodes(id) ON DELETE SET NULL;

NOTIFY pgrst, 'reload schema';
