-- Migration 426: Distribution — 话题实体绑定（publish_tasks.topic_refs）
--
-- 背景：发布页的话题输入框现在会去问抖音 creator 站自己的建议接口
--   GET https://creator.douyin.com/aweme/v1/search/challengesug/?aid=2906&keyword=<词>
-- 它每条建议都带一个 `cid` —— 平台的**话题实体 id**（新话题回空串）。
--
-- 为什么现在就存，尽管发布链还没用它
-- ==================================
-- 发布时话题依然是打进描述框的 `#词`，这一版**刻意不改**（勘探发现纯打字路径的
-- DOM 上没有实体节点，是强证据但不是定论 —— 要定论必须真发一条再回读作品的
-- text_extra）。但 `cid` 有一个性质：**它只在用户从下拉里选中的那一刻存在**，
-- 事后无法补（同一个词过一阵可能对上不同实体，播放量更是一直在涨）。
-- 不存 = 将来想验证"带 cid 发布 vs 纯打字发布有无差异"时，连对照组都没有。
--
-- 形状：jsonb 数组，每项 {"name": "...", "topic_id": "...", "view_count": 0}
--   - 与既有的 `topics`（纯名字数组，发布链真正读的那一份）**平行**，不替代它。
--     手打的话题没有 cid，本来就不会在这里出现，所以两者长度不必相等。
--   - `topic_id` 为空的条目在应用层就被丢掉（它不携带 topics 之外的任何信息），
--     所以库里出现的每一项都是"真的绑到了一个平台实体"。
--   - view_count 是**选中当刻**的累计播放量原始整数，同样是事后补不回来的量。
--
-- 不建 CHECK：这是记录性的装饰字段，形状由应用层 schema（TopicRef）保证；一条
-- 形状怪异的记录不该让一次本来合法的发布失败。
--
-- Idempotent: ADD COLUMN IF NOT EXISTS；重复执行无副作用。

BEGIN;

ALTER TABLE public.publish_tasks
  ADD COLUMN IF NOT EXISTS topic_refs JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN public.publish_tasks.topic_refs IS
  'Topic name -> platform topic entity id bindings captured at compose time: [{"name","topic_id","view_count"}]. topic_id is Douyin''s challenge `cid` from the creator suggest API; entries without one are dropped before insert. Parallel to `topics` (which is what the publish path actually reads) — NOT a replacement. Stored because the cid only exists at the moment the user picks a suggestion and cannot be reconstructed later.';

NOTIFY pgrst, 'reload schema';

COMMIT;
