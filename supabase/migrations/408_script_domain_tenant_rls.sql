-- 408_script_domain_tenant_rls.sql
--
-- RLS 第三层 (PR-1, 纯策略): 给 episodes / script_scenes / script_shots /
-- script_ops 加**团队级租户策略**,作为应用层授权(A 线 fail-closed 权限门 +
-- 服务端绑定 scope)之外的 DB 层兜底(agent 层 spec §3.2 ③)。
--
-- 现状: 这四张表当前各只有一条 `service_role` 全权策略;而后端以 `postgres`
-- 超级用户直连(BYPASSRLS),所以这些策略今天只对**直连 PostgREST/Realtime 的
-- 前端客户端**生效。本迁移补上团队成员的读写策略,PR-2 再让 agent 工具路径以
-- 非超级用户(authenticated)身份跑,兜底才真正覆盖后端 ORM 路径。
--
-- 租户锚点: episodes → projects(owner_id / team_id);script_scenes → script_id
-- → script_projects(team_id);script_shots → scene_id → script_scenes → …。
-- scenes/shots 无租户列,授权必须一路 join 到 script_projects,故封装成
-- SECURITY DEFINER STABLE helper(照抄同域现成的 `can_read_script_op` 范式,
-- mig 346),避免 RLS 递归 + 重复 join。
--
-- 策略是 permissive(与现有 service_role 策略 OR 叠加);postgres 超级用户
-- BYPASSRLS 无视全部,故编辑器 / DBOS 等现有 postgres 路径**不受影响**。
-- 幂等: 全部 CREATE OR REPLACE / DROP POLICY IF EXISTS。

-- ---------------------------------------------------------------------------
-- Helpers (SECURITY DEFINER: 绕过自身查询的 RLS,防递归;search_path 固定)
-- ---------------------------------------------------------------------------

-- episode 可访问: 其 project 的 owner 或该 project 团队的成员。
CREATE OR REPLACE FUNCTION public.can_access_episode(p_episode_id bigint)
  RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER
  SET search_path TO 'public'
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM episodes e
    JOIN projects p ON p.id = e.project_id
    WHERE e.id = p_episode_id
      AND (
        p.owner_id = auth.uid()
        OR p.team_id IN (SELECT get_user_team_ids(auth.uid()))
      )
  );
$$;

-- script(project) 可访问: 调用者是该 script 所属团队的成员。
CREATE OR REPLACE FUNCTION public.can_access_script(p_script_id bigint)
  RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER
  SET search_path TO 'public'
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM script_projects sp
    JOIN team_members tm ON tm.team_id = sp.team_id
    WHERE sp.id = p_script_id
      AND tm.user_id = auth.uid()
  );
$$;

-- scene 可访问: 经其 script → team_members(与现成 can_read_script_op 同构)。
CREATE OR REPLACE FUNCTION public.can_access_scene(p_scene_id bigint)
  RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER
  SET search_path TO 'public'
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM script_scenes s
    JOIN script_projects sp ON sp.id = s.script_id
    JOIN team_members tm ON tm.team_id = sp.team_id
    WHERE s.id = p_scene_id
      AND tm.user_id = auth.uid()
  );
$$;

-- ---------------------------------------------------------------------------
-- 表级 GRANT(RLS 只在有表级权限前提下才谈行过滤;幂等,生产已有,补全 fresh env)
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON public.episodes TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.script_scenes TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.script_shots TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.script_ops TO authenticated;

-- ---------------------------------------------------------------------------
-- 租户策略(FOR ALL: 读写同档 team 级;WITH CHECK 约束写入行也必须落在可访问租户)
-- ---------------------------------------------------------------------------
ALTER TABLE public.episodes      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.script_scenes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.script_shots  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.script_ops    ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS episodes_member_all ON public.episodes;
CREATE POLICY episodes_member_all ON public.episodes
  FOR ALL TO authenticated
  USING (public.can_access_episode(id))
  WITH CHECK (public.can_access_episode(id));

DROP POLICY IF EXISTS script_scenes_member_all ON public.script_scenes;
CREATE POLICY script_scenes_member_all ON public.script_scenes
  FOR ALL TO authenticated
  USING (public.can_access_script(script_id))
  WITH CHECK (public.can_access_script(script_id));

DROP POLICY IF EXISTS script_shots_member_all ON public.script_shots;
CREATE POLICY script_shots_member_all ON public.script_shots
  FOR ALL TO authenticated
  USING (public.can_access_scene(scene_id))
  WITH CHECK (public.can_access_scene(scene_id));

-- script_ops 已有 member SELECT 策略(mig 346);补写策略(INSERT/UPDATE/DELETE
-- 经 FOR ALL 覆盖,SELECT 与既有 permissive 策略 OR 叠加,无冲突)。
DROP POLICY IF EXISTS script_ops_member_write ON public.script_ops;
CREATE POLICY script_ops_member_write ON public.script_ops
  FOR ALL TO authenticated
  USING (public.can_read_script_op(scene_id))
  WITH CHECK (public.can_read_script_op(scene_id));

NOTIFY pgrst, 'reload schema';
