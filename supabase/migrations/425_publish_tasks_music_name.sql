-- Migration 425: Distribution — 抖音发布页的「选择音乐」（配乐）
--
-- 图文作品的配乐对分发影响很大，而在此之前我们发出去的每一条都走平台默认
-- （原声）：会话通道压根没碰过那个控件。这一列补上入口。
--
--   music_name —— 用户在我们表单里填的**曲名**。发布时浏览器打开发布页的
--     「选择音乐」弹窗、在搜索框里搜这个名字、从结果里选中。NULL = 不碰那个
--     控件，作品保持平台默认（原声）—— 也就是这一列存在之前每条作品的行为，
--     所以"没填"必须继续精确地表示这个意思。
--
--     没有 CHECK：与 self_declaration 不同，曲名不是平台的封闭词表，我们无从
--     校验它存不存在。存在性由浏览器侧在真实搜索结果里判定，搜不到回
--     `music_not_found` 类型化失败（**不**静默发一条没有音乐的作品 —— 用户
--     填了曲名却发出去没配乐，正是这个字段要消灭的那种"看起来成功了"）。
--
--     长度上界只在应用层（MAX_MUSIC_NAME_LEN），与 collection_name 同口径：
--     库里写死一个数字，平台哪天允许更长的曲名时改起来要动迁移。
--
-- Idempotent: ADD COLUMN IF NOT EXISTS；重复执行无副作用。

BEGIN;

ALTER TABLE public.publish_tasks
  ADD COLUMN IF NOT EXISTS music_name TEXT;

COMMENT ON COLUMN public.publish_tasks.music_name IS
  'Douyin 选择音乐: the track name to search for and select in the creator page''s music dialog. Matched against the platform''s own search results by the browser service (exact title preferred, otherwise the first result, reported either way); a search that returns nothing fails the row rather than publishing without music. NULL = leave the control untouched, i.e. the platform default (原声).';

NOTIFY pgrst, 'reload schema';

COMMIT;
