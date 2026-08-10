-- 419_episode_owner.sql
-- 集负责人(spec §5 方案 A):项目负责人在设置里指派;为空 = 未指派。
ALTER TABLE public.episodes
  ADD COLUMN IF NOT EXISTS owner_id UUID NULL REFERENCES auth.users(id) ON DELETE SET NULL;
COMMENT ON COLUMN public.episodes.owner_id IS 'Episode owner (workspace IA redesign); may edit node owner/schedule of this episode';
