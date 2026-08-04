-- Migration 403: scene numbering (agent-layer spec §4, plan A3)
--
-- Problem: script_scenes has NO number column today — only sort_order. The
-- displayed scene number is computed purely from array position (see
-- frontend/editor/components/SceneBlock.tsx `index + 1`), so it SHIFTS on
-- every insert. Shot ids are derived from scene numbers ("01-01" etc.) and
-- are the thing every downstream reference (VFX/editorial/human
-- conversation) actually points at — an unstable scene number means those
-- ids silently rot. A competitor product shipped exactly this bug: two
-- scenes in the same episode ended up both labelled "01", and its own agent
-- had to stop and ask a human "tell me what number this scene actually is."
--
-- Fix (screenwriting-industry practice, not a new invention):
--   - writing phase (numbering_locked_at IS NULL): the number is DERIVED
--     from canonical order (chapter_id NULLS LAST, sort_order ASC), never
--     written to scene_number. This is today's behavior, just given a name.
--   - lock (production draft): freeze — write every scene's derived number
--     into scene_number. From this instant numbers never change.
--   - insert after lock: the new scene gets a letter-suffixed number (3A,
--     3B, ...) — existing numbers are NEVER renumbered.
--   - delete after lock: the row and its number are KEPT, just marked
--     omitted_at (the printed-script "3 OMITTED" convention) — the number
--     stays reserved forever so no downstream reference rots.
--
-- numbering_locked_at lives on script_projects (one lock per script/episode
-- draft, not per scene). scene_number + omitted_at live on script_scenes.
--
-- Existing rows: scene_number and omitted_at are NULL for every row today —
-- that IS "still in writing phase," so no backfill is needed or correct to
-- do (backfilling would freeze numbers nobody asked to freeze).

ALTER TABLE public.script_projects
  ADD COLUMN IF NOT EXISTS numbering_locked_at timestamptz NULL;

COMMENT ON COLUMN public.script_projects.numbering_locked_at IS
  'NULL = writing phase (scene numbers derived from order, never stored). '
  'Set once, at lock time, to freeze every script_scenes.scene_number under '
  'this script — a one-way transition, never cleared back to NULL.';

ALTER TABLE public.script_scenes
  ADD COLUMN IF NOT EXISTS scene_number text NULL;

COMMENT ON COLUMN public.script_scenes.scene_number IS
  'NULL while the parent script is unlocked (number is derived from '
  'position, not stored). Once script_projects.numbering_locked_at is set, '
  'this is the authoritative, never-renumbered display number: a plain '
  'integer string ("3") for an original locked scene, or an integer + '
  'uppercase-letter suffix ("3A", "3B", ...) for a scene inserted after '
  'lock. Injected to agents/tools as scene_no_in_episode — never as a '
  'vague "current_scene" (that ambiguity is exactly what broke a '
  'competitor product: a view-position index masquerading as the scene''s '
  'own number).';

ALTER TABLE public.script_scenes
  ADD COLUMN IF NOT EXISTS omitted_at timestamptz NULL;

COMMENT ON COLUMN public.script_scenes.omitted_at IS
  'Set instead of hard-deleting a scene once the script''s numbering is '
  'locked (the printed-script "3 OMITTED" convention) — the row and its '
  'scene_number are kept so downstream references never rot. NULL for a '
  'live scene, or for any scene deleted before lock (those are still hard '
  'DELETEd — nothing to preserve when no number was ever assigned).';

-- Safety net for the "episode-scoped, unique" contract (spec §4.3): a
-- partial unique index, not a NOT NULL/CHECK, because scene_number is
-- legitimately NULL for every unlocked scene and for a freshly-created
-- (not yet positioned) scene in an already-locked script. Scoped by
-- script_id, not episode_id, because script_projects.episode_id is
-- NULLable (a script need not be attached to an episode yet) and, in the
-- current data model, get_or_create_for_episode's advisory lock keeps AT
-- MOST one non-deleted script per episode as a practical (not DB-enforced)
-- invariant — so script_id IS the episode-scoped unit for numbering today.
-- Revisit if that invariant is ever relaxed to allow multiple scripts per
-- episode.
CREATE UNIQUE INDEX IF NOT EXISTS idx_script_scenes_number_unique
  ON public.script_scenes (script_id, scene_number)
  WHERE scene_number IS NOT NULL;

NOTIFY pgrst, 'reload schema';
