-- 417 — social_accounts：放行 Realtime 投递，同批把凭证列收口到列级授权
--
-- P0-3（docs/superpowers/specs/2026-08-09-distribution-gap-closure-plan.md）。
--
-- ── 为什么 413 没有修好这个问题 ─────────────────────────────────────────
--
-- 413 把 social_accounts 加进了 supabase_realtime publication，然后**以
-- publication 里有这张表为验收**宣布修好。用户侧毫无变化：绑定成功后卡片
-- 仍然不出现，仍然要手动刷新（同一现象已反馈三次）。
--
-- publication 是必要条件，不是充分条件。真正的门槛在下一层：Realtime 的
-- postgres_changes **以订阅者的身份逐行做 RLS 检查**。这不是文档推断，是
-- 本库里 `realtime.apply_rls()` 的源码（supabase/realtime v2.76.5）：
--
--     perform set_config('role', trim(both '"' from working_role::text), true),
--             set_config('request.jwt.claims', claims::text, true);
--     execute 'execute walrus_rls_stmt' into subscription_has_access;
--     if subscription_has_access then
--         visible_to_subscription_ids = visible_to_subscription_ids || subscription_id;
--
-- 而 `walrus_rls_stmt` 就是 `select exists(select 1 from <表> where <主键>=…)`。
-- 换句话说：**订阅者自己 SELECT 不到那一行，事件就不投递**。
--
-- social_accounts 此前唯一的策略是 `auth.role() = 'service_role'`，所以
-- authenticated 能 SELECT 到的行数是 0 —— 订阅建得起来（`SUBSCRIBED`），
-- 事件一条都收不到。实测（修复前，claude.debug 真实登录 + 真实 UPDATE）：
--
--     [07:53:23] SIGNIN ok  uid=b2180063-…  role=authenticated
--     [07:53:23] SUB    table=social_accounts  status=SUBSCRIBED
--     [07:53:31] (另一条连接) UPDATE public.social_accounts SET updated_at=now() … → UPDATE 1
--     [07:53:45] DONE   total events received = 0
--
-- 对照 task_tracking：它的策略是 `auth.uid() = user_id`，所以它的 realtime
-- 一直好用。差别只在这一层。
--
-- ── 为什么放行必须和收口同批做 ─────────────────────────────────────────
--
-- 这张表存着 Fernet 密文：access_token / refresh_token / session_state。
-- 此前挡住它们的**只有那条 service_role 策略** —— 表级 GRANT 是敞开的：
--
--     anon           DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
--     authenticated  DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
--
-- 一旦加一条 authenticated 能命中的 SELECT 策略，护栏就没了：三列密文会经
-- PostgREST 被前端直接拉走，也会进 Realtime 的 payload。所以「加策略」和
-- 「把 SELECT 缩到列级」必须是同一个迁移，中间不能有敞口窗口。
--
-- 列级授权在 Realtime 侧是**被支持且是内建行为**，同样有源码为证 ——
-- `apply_rls()` 对每一列取 `pg_catalog.has_column_privilege(...)` 填
-- `is_selectable`，然后 record / old_record 都按 `where (c).is_selectable`
-- 过滤。没授权的列直接不进 payload。
--
-- ⚠️ 但有一个硬约束，写在同一段源码里：
--
--     elsif action <> 'DELETE' and sum(c.is_selectable::int) <> count(1)
--           from unnest(columns) c where c.is_pkey then
--         … array['Error 401: Unauthorized']
--
-- **主键列必须在授权列表里**，否则整条事件退化成 401，一个订阅者都收不到。
-- 所以下面的 GRANT 列表第一列是 id，删它等于把这次修复整个作废。
--
-- ── REPLICA IDENTITY 保持 default，不动 ────────────────────────────────
--
-- 改 FULL 会把整行旧值（含三列密文）写进 WAL。前端只需要知道「某行变了」
-- 然后重新拉列表，不需要 old_record，default（仅主键）足够且更安全。
-- 实测当前是 'd'，本迁移不碰它。

-- ── 1. anon 完全退场 ──────────────────────────────────────────────────
--
-- 匿名身份没有任何理由碰这张表：绑定、列表、发布全都要登录态，后端自己走
-- SQLAlchemy 直连（read_scope/write_scope，postgres 角色），从不以 anon 或
-- authenticated 身份读写。那套 GRANT ALL 是建表时的默认敞口，不是需求。
REVOKE ALL ON public.social_accounts FROM anon;

-- ── 2. authenticated 从「全表全权限」缩到「非密文列的 SELECT」 ─────────
--
-- 先 REVOKE ALL 再按列 GRANT：只加 GRANT 而不撤旧的表级 SELECT 是无效收口
-- —— 表级 SELECT 存在时 has_column_privilege 对每一列都返回 true，等于没改。
--
-- 写权限（INSERT/UPDATE/DELETE）一并撤掉：所有写入都在后端 workflow 里
-- （upsert_session_account / upsert_account / 软删），没有任何客户端直写路径。
REVOKE ALL ON public.social_accounts FROM authenticated;

-- 授权列 = 全部列 − {access_token, refresh_token, session_state}，
-- 与 social_accounts_repository._SECRET_COLS 一字不差，也就是与 REST 接口
-- 早就在返回的 _public_row 形状一致。新增列默认**不**在授权列表里（fail
-- closed）—— 这张表以后加的列更可能是凭证而不是展示字段，默认拒绝是对的；
-- 真需要透出时在新迁移里显式补一行 GRANT。
GRANT SELECT (
    id,                  -- ⚠️ 主键，见上文的 Error 401：删了整条事件作废
    scope_type,
    scope_id,
    platform,
    platform_user_id,
    username,
    avatar_url,
    token_expires_at,    -- 过期**时刻**，不是 token 本身
    status,
    created_by,
    created_at,
    updated_at,
    auth_type,
    session_checked_at,
    platform_handle,
    deleted_at
) ON public.social_accounts TO authenticated;

-- ── 3. SELECT 策略：与 REST 的可见范围逐字对齐 ────────────────────────
--
-- 口径直接抄 SocialAccountsRepository.list_for_user：user scope 看
-- scope_id = 自己，team scope 看自己所在的 team。**不是**只写
-- `auth.uid() = created_by` —— 那样 team 账号的其他成员在列表里看得到卡片，
-- 却收不到它的 realtime 事件，于是这个 bug 会以「团队账号不刷新」的形态
-- 原样复现一次。created_by 这一支仍然保留：绑定者对自己绑过的行始终可见。
--
-- get_user_team_ids_text 是现成的 STABLE SECURITY DEFINER 函数（search_path
-- 已 pin，authenticated 有 EXECUTE），mig 408 起就是团队 RLS 的统一入口，
-- 直接 JOIN team_members 会因为那张表自身的 RLS 递归。
--
-- 只给 SELECT。写策略一条都不加：写入没有客户端路径（见上），而 service_role
-- 那条 FOR ALL 策略继续覆盖后端。
DROP POLICY IF EXISTS "Authenticated read own social accounts" ON public.social_accounts;
CREATE POLICY "Authenticated read own social accounts"
    ON public.social_accounts
    FOR SELECT
    TO authenticated
    USING (
        auth.uid() = created_by
        OR (scope_type = 'user' AND scope_id = auth.uid()::text)
        OR (
            scope_type = 'team'
            AND scope_id IN (SELECT public.get_user_team_ids_text(auth.uid()))
        )
    );

COMMENT ON POLICY "Authenticated read own social accounts" ON public.social_accounts IS
    'Realtime postgres_changes 以订阅者身份做 RLS 检查（realtime.apply_rls → '
    'walrus_rls_stmt），没有这条策略 authenticated 可见 0 行，事件一条都不投递 —— '
    '绑定成功后账号卡片不出现、要手动刷新的根因。可见范围与 '
    'SocialAccountsRepository.list_for_user 对齐。凭证三列不靠这条策略保护，'
    '靠的是同迁移里的列级 GRANT（authenticated 对 access_token / refresh_token / '
    'session_state 没有 SELECT 授权）。';
