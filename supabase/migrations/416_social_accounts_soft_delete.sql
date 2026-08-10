-- 416 — social_accounts.deleted_at: 「移除账号」改软删，发布历史不再被连坐
--
-- P0-2（docs/superpowers/specs/2026-08-09-distribution-gap-closure-plan.md）。
--
-- 现状是硬删，而这张表被两条 ON DELETE CASCADE 的外键指着：
--
--     publish_task_accounts.account_id  →  ON DELETE CASCADE
--     account_environments.account_id   →  ON DELETE CASCADE
--
-- 也就是说 UI 上那个没有任何确认对话框的 `Remove` 按钮，一次点击会同时抹掉
-- 该账号的**全部发布记录**。删除前实测（2026-08-09 生产库）：
--
--     id               | username | 会被连带删掉的发布记录
--     335617669826935  | MioPoo   | 10
--     336553799171178  | MioPoo    | 0
--
-- 用户当时点掉的恰好是 0 条那行 —— 是运气，不是设计。
--
-- 发布记录是资产（谁在什么时候往哪个平台发了什么，是审计与复盘的唯一来源），
-- 它不该随着「我不想再用这个账号了」一起消失。所以语义从「删除账号」改成
-- 「解绑账号」：行留下，凭证与展示都退场。
--
-- ⚠️ 两条外键的 CASCADE **不动**。软删之后 DELETE 不再发生，CASCADE 就是一条
-- 走不到的分支；真要 hard delete（GDPR 类请求）时它仍然是正确的行为。把它改成
-- SET NULL 反而会让 publish_task_accounts.account_id 变得可空，波及那张表所有的
-- JOIN 与非空假设 —— 那是另一件事，不是本次修复。

ALTER TABLE public.social_accounts
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

COMMENT ON COLUMN public.social_accounts.deleted_at IS
    '解绑时间。NULL = 在用。非 NULL 的行对所有业务读路径不可见'
    '（list_for_user / get_public / get_with_tokens / get_with_session /'
    ' list_session_accounts_for_check 全部带 deleted_at IS NULL），'
    '但 publish_task_accounts 仍然 JOIN 得到它 —— 发布历史要显示当时用的是'
    '哪个账号。唯一键 (scope_type, scope_id, platform, platform_user_id) 不含'
    '本列：软删行仍占着键，重新扫码绑定同一身份时由 upsert 命中原行并把'
    'deleted_at 清空（唤醒），而不是撞唯一键报错或新建一行。';

-- 存量数据：全部保持 NULL（= 在用）。这是 ADD COLUMN 的默认结果，不需要 UPDATE。
-- 库里此刻只有一行 social_accounts，且它是活的。
--
-- 没有加 `WHERE deleted_at IS NULL` 的部分索引：现有 idx_social_accounts_scope
-- (scope_type, scope_id) 已经覆盖 list_for_user 的选择性，而这张表的量级是
-- 「每个用户几个账号」，多一个索引只是写放大。等真有几万行再说。
