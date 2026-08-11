-- 422_storyboard_canvas_soft_delete_index.sql
-- 修复轮1(评审 Important #1): 421 的 uq_canvases_storyboard_per_episode
-- 没有排除软删行。generic ``DELETE /canvases/{id}`` 不区分 kind,会把一张
-- storyboard 画布软删(deleted_at 置位)——但那一行仍占着索引里的
-- (project_id, episode_id) 槽位。之后 get-or-create 的读侧过滤
-- deleted_at IS NULL 读不到它,写侧 INSERT 却撞在这条仍然"活"的唯一
-- 索引上 → 23505 → 应用层重读(同样过滤 deleted_at IS NULL)还是 None →
-- 500,且这个状态不会自愈,该集的分镜画布永久坏死。
--
-- 先例:349_inspiration_notes.sql 的部分唯一索引就是 WHERE ... AND
-- deleted_at IS NULL 这个形状,这里对齐同一约定。

DROP INDEX IF EXISTS public.uq_canvases_storyboard_per_episode;

CREATE UNIQUE INDEX IF NOT EXISTS uq_canvases_storyboard_per_episode
  ON public.canvases (project_id, episode_id)
  WHERE kind = 'storyboard' AND deleted_at IS NULL;

NOTIFY pgrst, 'reload schema';
