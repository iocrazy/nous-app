-- 496 — 给 4 个被 RLS 策略引用的 SECURITY DEFINER 身份助手加 auth.uid() 守卫
--
-- 491 的续票。491 收回了 8 个后端专用 definer 函数对浏览器角色的 EXECUTE，并
-- 刻意留下这 4 个：
--   is_conversation_member(uuid, bigint)
--   conversation_member_joined_at(uuid, bigint)
--   get_user_team_ids(uuid)
--   get_user_team_ids_text(uuid)
-- 形状与 491 那 8 个相同（CLAUDE.md「SECURITY DEFINER 函数不能既收调用方给的身份、
-- 又对浏览器开放」）：definer 绕过 RLS、目标用户 uuid 是普通参数、anon 与
-- authenticated 都有 EXECUTE（2026-09-23 生产逐个核过，函数体里也没有 auth.uid()）。
-- 于是拿着烤进浏览器包的 anon key，POST /rest/v1/rpc/get_user_team_ids 传任意 uuid，
-- 就能问出「某人属于哪些团队 / 是否在某会话里 / 何时加入」。
--
-- 为什么不 REVOKE（三选一的第二种：浏览器要用 → 函数体内守卫）：
--   * 它们被 49 条 RLS 策略引用（61 处调用，按 schema-drift 同法重放后的 pg_policies
--     统计；teams / team_members / libraries / conversations /
--     conversation_members / messages / message_* / script_* / skills / tags / ...），
--     策略以**调用者**身份求值；除 417 的 social_accounts 那条是 TO authenticated 外，
--     其余 48 条都没写 TO，作用于 PUBLIC（含 anon）。收回
--     authenticated 或 anon 的 EXECUTE 会让这些表上的普通查询（含 Realtime 的
--     逐行 RLS 检查）直接 permission denied。
--   * 每一条策略传的 uuid 实参都是 auth.uid()（基线里是 `( SELECT auth.uid() )`，
--     408 的 can_access_episode 与 417 / 468 的策略是 `auth.uid()`）—— 策略从不
--     替别人问。所以「参数必须等于 auth.uid()」对策略是恒真的，只关掉直调那条路。
--   * 后端（Supavisor 以 postgres 直连）不调这 4 个函数：agent_memory 的团队查询是
--     自己的一句 SELECT team_id FROM team_members。但为了不在将来某个后端 / 内部
--     调用者替任意用户查询时静默返回空，受信的服务端角色照旧放行（见下）。
--
-- 守卫（逐字，四个函数一样）：
--     p_user = auth.uid()
--     OR current_setting('role') NOT IN ('anon', 'authenticated')
--
--   * 为什么看 current_setting('role') 而不是 current_user：函数是 SECURITY DEFINER，
--     函数体里 current_user 永远是属主（postgres），恒真，等于没守（pg17 实测：
--     SET LOCAL ROLE anon 后调 definer，体内 current_user=postgres、role=anon）。definer 切换的是
--     内部 user id，不改 `role` 这个 GUC —— 它仍是调用方 `SET [LOCAL] ROLE` 的值：
--     PostgREST 每个请求都会 SET LOCAL ROLE anon / authenticated / service_role；
--     Realtime 的逐行 RLS 检查 set_config('role','authenticated')；后端 caller_scope
--     SET LOCAL ROLE authenticated；后端直连不 SET ROLE，值是 'none'。
--   * 为什么不看 auth.role() / JWT 里的 role：不带 Authorization 头打 PostgREST 时
--     没有 JWT，claims 为空，auth.role() 是 NULL —— 与「后端直连」同形，用它判受信
--     就把直调那条路又开回去了。role GUC 在那种请求里是 'anon'。
--   * 为什么是黑名单两项而不是白名单：浏览器能到达的角色只有 authenticator 能切换
--     过去的 anon / authenticated / service_role，后者本就受信；白名单反而要穷举
--     postgres / 'none' / supabase_* 等内部角色，漏一个就让某个内部调用者静默拿空。
--   * NULL：anon 请求里 auth.uid() 是 NULL，`p_user = NULL` 为 NULL，OR false 仍是
--     NULL，WHERE 不成立 → 空集 / false / NULL。策略里 anon 的结果本来就是空（没有
--     user_id IS NULL 的成员行），行为不变。
--
-- 性能：这些函数在策略里逐行调用。守卫只读两个 GUC（auth.uid() 是 STABLE 的
-- current_setting），不加查询；STABLE / SECURITY DEFINER / SET search_path 原样保留。
--
-- 签名、参数名、返回类型都不变 → CREATE OR REPLACE 原地替换，策略无需重建，
-- 现有 ACL（含 service_role 的授权）原样保留。幂等：重复执行得到同一函数体。
-- 空库安全：函数体只引用基线就有的 team_members / conversation_members。

CREATE OR REPLACE FUNCTION public.is_conversation_member(p_user uuid, p_conversation bigint)
    RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.conversation_members
    WHERE conversation_id = p_conversation AND member_type = 'user' AND user_id = p_user
      AND (p_user = auth.uid() OR current_setting('role') NOT IN ('anon', 'authenticated'))
  );
$$;

CREATE OR REPLACE FUNCTION public.conversation_member_joined_at(p_user uuid, p_conversation bigint)
    RETURNS timestamp with time zone
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
AS $$
  SELECT joined_at FROM public.conversation_members
  WHERE conversation_id = p_conversation AND member_type = 'user' AND user_id = p_user
    AND (p_user = auth.uid() OR current_setting('role') NOT IN ('anon', 'authenticated'));
$$;

CREATE OR REPLACE FUNCTION public.get_user_team_ids(p_user_id uuid)
    RETURNS SETOF bigint
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
AS $$
  SELECT DISTINCT team_id FROM public.team_members
  WHERE user_id = p_user_id
    AND (p_user_id = auth.uid() OR current_setting('role') NOT IN ('anon', 'authenticated'));
$$;

CREATE OR REPLACE FUNCTION public.get_user_team_ids_text(p_user_id uuid)
    RETURNS SETOF text
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
AS $$
  SELECT DISTINCT team_id::text FROM public.team_members
  WHERE user_id = p_user_id
    AND (p_user_id = auth.uid() OR current_setting('role') NOT IN ('anon', 'authenticated'));
$$;
