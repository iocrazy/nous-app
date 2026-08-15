-- Migration 429: Distribution — 配乐的结构化身份（publish_tasks.music_ref）
--
-- 为什么曲名不够用
-- ================
-- `music_name`（mig 425）存的是曲名原文，浏览器侧拿它去发布页的音乐弹窗里搜，
-- 搜到同名那行就点。用户手打歌名时这是合理的——他本来就只知道一个名字。
--
-- 但实测（2026-08-15）说明**曲名不是身份**：
--   * 搜「起风了」一页里 5 条标题完全相同、id 各异、使用量 0～30023 不等；
--   * 拿分类榜里 3 首曲子按曲名去搜，3/3 都搜不到那一首，其中一条还返回了一个
--     **标题一模一样但 id 不同**的歌。
-- 后者是最坏的形态：按名匹配判「精确命中」，点击成功，读回校验也过——每一道
-- 关卡都亮绿灯，发出去的是另一首歌。配乐发出去之后平台不让换。
--
-- 形状：jsonb 对象，NULL = 没有结构化引用（手打曲名的老路径）
--   {"music_id","music_name","music_author","duration","user_count","cover_url"}
--   - `music_id` 是抖音搜索结果的 **`id_str`**（不是 `id`）。上游同时给两个字段，
--     `id` 是 JSON number 且超过 2^53（实测 6953836671917951012），经 JSON 进
--     前端就会精度丢失——这是 CLAUDE.md「Snowflake BIGINT 精度丢失」的外部版本。
--     两者并存是真实形状，不是可以「统一」的漂移。
--   - `music_name` 与 `music_name` 列**平行存在**：那一列仍是发布链的搜索关键词
--     与老路径的唯一输入，这里的副本是指纹的一部分（名+作者+时长三元组）。
--   - `duration` 是秒（整数），`user_count` 是选中当刻的使用人数原始整数。
--     两者都是事后补不回来的量：使用量一直在涨，而「用户当时挑的是 3 万人用的
--     那一首」正是他挑它的理由。
--
-- `music_id` 今天**还不能**直接用来点中弹窗里的行（行上有没有 id 属性未实测），
-- 但它是唯一真正的身份，且已实测在发布侧可解析（creator 站 music/info 接口
-- 5/5 命中）。存下来，等真去量了弹窗 DOM，它决定"按 id 点中"是一周还是从头开始。
--
-- 不建 CHECK：形状由应用层 schema（MusicRef）保证。一条形状怪异的历史记录不该
-- 让一次本来合法的发布失败——同 topic_refs（mig 426）的口径。
--
-- Idempotent: ADD COLUMN IF NOT EXISTS；重复执行无副作用。

BEGIN;

ALTER TABLE public.publish_tasks
  ADD COLUMN IF NOT EXISTS music_ref JSONB;

COMMENT ON COLUMN public.publish_tasks.music_ref IS
  'Structured identity of the track picked from the platform''s own catalogue: {"music_id","music_name","music_author","duration","user_count","cover_url"}. music_id is Douyin''s `id_str` (the sibling `id` is a JSON number past 2^53 and loses precision in JS). NULL = no structured reference (the hand-typed music_name path). Parallel to the music_name column, which stays the search keyword — this object is the fingerprint the browser aligns rows against so that a same-titled different upload cannot be published as a match.';

NOTIFY pgrst, 'reload schema';

COMMIT;
