-- Migration 407: Distribution — 抖音发布页缺的三个表单字段（自主声明 / 定时 / 合集）
--
-- 会话通道（spec 2026-08-04）真的替用户操作发布页之后，页面上有而我们没有的
-- 字段就成了功能缺口。这次补三项：
--
--   self_declaration —— 抖音「自主声明」下拉。合规字段，存的是页面上的
--     **中文原文**（六个之一），不是我们发明的 code：浏览器侧靠这串文本做
--     DOM 匹配，中间任何一次翻译都会让选项静默选不上。UI 上的英文由 i18n
--     负责显示，与存储值无关。CHECK 把六个原文钉死在库里 —— 平台改文案时
--     写入会直接失败，好过静默发出一个没有声明的作品。
--
--     与既有的 ai_content 的关系（产品决策，见
--     backend/app/services/distribution/publish_options.py::resolve_self_declaration）：
--     ai_content 从 356 起就存在但从未送到发布页上。现在的口径是
--     **自动映射 + 允许覆盖** —— self_declaration 为 NULL 且 ai_content=TRUE
--     时，发布时补 '内容由AI生成'；显式选了别的就以显式的为准。两列都保留：
--     ai_content 是"这是不是 AI 作品"的事实，self_declaration 是"页面上选了
--     哪一项"的动作，合并成一列会丢掉其中一个语义。
--
--     NULL 与 '无需添加自主声明' 不等价：NULL = 不碰那个控件（平台默认），
--     字面量 = 用户显式选了"不加声明"。
--
--   collection_name —— 抖音「合集」。按名称选**已有**合集（浏览器侧在下拉里
--     找同名项），不创建新合集；找不到由浏览器侧回类型化失败。
--
--   scheduled_at —— 列在 356 就建好了（注释写着 "D3 scheduling; D2 always
--     NULL"），这次只是终于有入口写它。平台窗口是「2 小时后 ~ 14 天内」，
--     那是相对 now() 的约束，CHECK 表达不了，校验在应用层三处（请求 schema /
--     起浏览器前 fail-fast / 前端提交前）。这里只更新注释，避免下一个人再
--     照着 356 的注释以为它仍是死列。
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + DROP CONSTRAINT IF EXISTS；重复执行无副作用。

BEGIN;

ALTER TABLE public.publish_tasks
  ADD COLUMN IF NOT EXISTS self_declaration TEXT,
  ADD COLUMN IF NOT EXISTS collection_name TEXT;

ALTER TABLE public.publish_tasks DROP CONSTRAINT IF EXISTS publish_tasks_self_declaration_check;
ALTER TABLE public.publish_tasks ADD CONSTRAINT publish_tasks_self_declaration_check
  CHECK (self_declaration IS NULL OR self_declaration IN (
    '内容由AI生成',
    '内容为个人观点或见解',
    '内容为转载信息',
    '内容含营销推广信息',
    '虚构演绎，仅供娱乐',
    '无需添加自主声明'
  ));

COMMENT ON COLUMN public.publish_tasks.self_declaration IS
  'Douyin 自主声明: one of the six verbatim platform labels, or NULL = leave the control untouched. NULL is NOT the same as 无需添加自主声明 (an explicit user choice). Auto-filled from ai_content at publish time when NULL — see publish_options.resolve_self_declaration.';

COMMENT ON COLUMN public.publish_tasks.collection_name IS
  'Douyin 合集 name to file the post under. Matched by exact name against the account''s EXISTING collections by the browser service; never creates one. NULL = no collection.';

COMMENT ON COLUMN public.publish_tasks.scheduled_at IS
  'Platform-side scheduled publish time (session channel sets it in the creator page). Window is 2h..14d from submission — enforced in the app layer (relative to now(), not expressible as a CHECK). NULL = publish immediately.';

NOTIFY pgrst, 'reload schema';

COMMIT;
