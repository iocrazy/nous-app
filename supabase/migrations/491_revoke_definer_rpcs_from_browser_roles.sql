-- 491 — 收回 8 个后端专用 SECURITY DEFINER 函数对浏览器角色的执行权
--
-- 形状与 mig 274 → 463 那次一模一样（CLAUDE.md「SECURITY DEFINER 函数不能既收
-- 调用方给的身份、又对浏览器开放」）：函数以属主身份绕过 RLS，目标用户 / 团队 id
-- 是普通参数，而 anon 与 authenticated 都有 EXECUTE —— anon key 是烤进浏览器包的
-- 公开值，PostgREST 的 /rest/v1/rpc/<name> 直接可达，换个 id 就能读写别人的数据。
--
-- 2026-09-23 在生产逐个核过（has_function_privilege）：下列 8 个对 anon 与
-- authenticated 都是 true，函数体里都没有 auth.uid() 守卫。最重的两个是积分：
--   rpc_refund_team_points_idempotent —— 任何人可给任意团队加任意积分
--   rpc_consume_team_points           —— 任何人可扣任意团队的积分
-- 其余会按 user id 泄露媒体标题 / 封面 / 作者 / 标签计数，或按 id 重排分镜帧。
--
-- 为什么 REVOKE 而不是在函数里加 auth.uid() 守卫（CLAUDE.md 三选一的第一种）：
-- 这 8 个的调用方**只有后端**——前端 / admin / 扩展零引用，也没有任何 RLS 策略
-- 引用它们（生产 pg_policies 已查）。后端经 Supavisor 以 `postgres`（属主）连库
-- （SUPAVISOR_DATABASE_URL 的用户是 postgres.heygo-prod），不依赖 PUBLIC 的默认
-- 授权，所以收回 PUBLIC / anon / authenticated 对后端零影响；service_role 的显式
-- 授权保留不动。
--
-- 刻意不在本迁移里的：is_conversation_member / conversation_member_joined_at /
-- get_user_team_ids / get_user_team_ids_text 同样是 definer + anon 可执行，但它们
-- 被 RLS 策略引用 —— 策略以调用者身份求值，直接收回会让正常查询失败。它们只泄露
-- 「某个 uuid 属于哪些团队 / 会话」这类布尔 / id，另开一票按策略逐个处理。
--
-- 幂等：REVOKE 对已收回的权限是空操作。to_regprocedure 守卫让本迁移在函数不存在
-- 的库上（例如 schema-drift 的空库若将来删了某个函数）也不报错。

DO $$
DECLARE
    fn text;
    sig regprocedure;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'public.rpc_refund_team_points_idempotent(bigint,uuid,integer,text,text,text)',
        'public.rpc_consume_team_points(bigint,uuid,integer,boolean)',
        'public.find_duplicate_videos(uuid,double precision,integer)',
        'public.get_cleanup_data(uuid,integer,integer,integer)',
        'public.get_cleanup_stats(uuid)',
        'public.get_cleanup_suggestions(uuid,integer,integer,integer)',
        'public.get_user_tag_counts(uuid,integer)',
        'public.rpc_reorder_storyboard_frames(uuid[])'
    ]
    LOOP
        sig := to_regprocedure(fn);
        IF sig IS NULL THEN
            RAISE NOTICE '491: % not found, skipping', fn;
            CONTINUE;
        END IF;
        EXECUTE format(
            'REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC, anon, authenticated',
            sig
        );
    END LOOP;
END
$$;
