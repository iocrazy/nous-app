-- 421_storyboard_canvas.sql
-- 每集一张系统分镜画布(shot-nodes-on-canvas spec 2026-08-11 §2):
-- kind='storyboard' + episode_id,一个 episode 至多一张 storyboard 画布。
--
-- ``canvases.kind`` already exists (mig 280, widened by 357/358/362) with a
-- CHECK constraint enumerating the allowed values — this migration only
-- widens that CHECK to add 'storyboard' and adds the new episode_id FK.
-- ``ADD COLUMN IF NOT EXISTS kind ...`` is intentionally NOT repeated here
-- (a stale draft of this migration proposed re-adding it with a different
-- default, which would have been a no-op against the existing column but
-- misleading to read).

ALTER TABLE public.canvases
  ADD COLUMN IF NOT EXISTS episode_id BIGINT NULL
    REFERENCES public.episodes(id) ON DELETE CASCADE;

ALTER TABLE public.canvases DROP CONSTRAINT IF EXISTS canvases_kind_check;
ALTER TABLE public.canvases
  ADD CONSTRAINT canvases_kind_check
  CHECK (kind IN ('smart', 'lite', 'classic', 'character', 'location', 'prop', 'storyboard'));

-- Idempotence guarantee for the get-or-create endpoint: a concurrent
-- duplicate INSERT for the same (project_id, episode_id) storyboard canvas
-- hits this partial unique index and raises 23505, which the repository
-- catches and resolves by re-reading the row the other request created.
CREATE UNIQUE INDEX IF NOT EXISTS uq_canvases_storyboard_per_episode
  ON public.canvases (project_id, episode_id) WHERE kind = 'storyboard';

COMMENT ON COLUMN public.canvases.episode_id IS
  'FK to episodes; set only when kind=''storyboard'' (system per-episode shot canvas, spec 2026-08-11)';

NOTIFY pgrst, 'reload schema';
