-- 470_username_unique_and_editable.sql
--
-- 主账号名：系统发一个默认的，用户可以改，但全局唯一。
--
-- 用户的裁定（2026-09-15）：「生成默认的用户名，用户可以更改，但是全局唯一」，
-- 参照巨量引擎账号中心的形状 —— 主账号名 + 一个稳定的数字 ID。
--
-- 数据模型本来就对得上：`user_profiles.username` 是主账号名，`display_id`
-- (BIGINT snowflake, UNIQUE) 就是那个 ID。缺的是三件里的后两件。
--
-- WHAT WAS MISSING
-- ================
-- ① 用户改不了。全仓库唯一写 `user_profiles.username` 的地方是注册那一下。
--    `PUT /api/v1/auth/me` 收 `username` 参数，但它只写进 Supabase Auth 的
--    `raw_user_meta_data` —— 那个带 UNIQUE 约束、团队成员列表和积分榜真正显示
--    的列纹丝不动。一个看着能改名、实际改到别处去的接口，比没有更糟。
--    （这一半由本 PR 的后端补上，迁移只负责数据库这一侧。）
--
-- ② 唯一性是大小写敏感的。`UNIQUE(username)` 让 `iocrazy` 和 `IOCrazy` 同时
--    存在 —— 对一个用来认人的主账号名，那是可以冒充的。
--
-- ③ 有账号根本没有名字。生产此刻 1 行 `username IS NULL`（正是主账号）。
--
-- ORDERING SAFETY
-- ===============
-- 迁移与后端部署没有先后保证（CLAUDE.md 已知缺口），两种顺序都安全：
--   * 迁移先落地 → 大小写唯一索引生效，旧后端照常工作（它压根不写这个列）。
--   * 后端先上线 → 新接口调 `public.unique_username()`；函数还不存在时
--     `SQLSTATE 42883`，后端把它转成 503 而不是静默失败（见 router 注释）。

-- ---------------------------------------------------------------------------
-- 1. 「把这个名字变唯一」收成一个函数
-- ---------------------------------------------------------------------------
-- 注册有**两条**路径 —— `handle_new_user()` 触发器，和后端的
-- `supabase_auth_router._bootstrap_user()`（JWT 首次落地时补建 profile）。
-- mig 469 只修了触发器那条；Python 那条至今还是
-- `username = meta['username'] or email.split('@')[0] or 'user'` 外加一个
-- `except Exception: logger.warning` —— 于是第二个没有邮箱的人拿到重复的
-- 'user'，撞唯一约束，异常被吞掉，**profile 悄悄没建成**。
--
-- 两处各写一遍逻辑就是让它们再次漂移。收成一个 SQL 函数，两边都调它。
CREATE OR REPLACE FUNCTION public.unique_username(p_base text, p_uid uuid)
RETURNS text
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  base      text;
  candidate text;
BEGIN
  -- 空基名不是「没意见」，是「没有名字」—— 那会拼出一个 "'s Workspace" 的团队
  -- 和一个没人叫得出的账号。uuid 短码保证一定有个名字。
  base := NULLIF(btrim(COALESCE(p_base, '')), '');
  IF base IS NULL THEN
    base := 'user_' || substr(replace(p_uid::text, '-', ''), 1, 8);
  END IF;

  -- 自己已经叫这个名字 → 原样返回（改名接口的幂等重试会走到这里）。
  IF EXISTS (
    SELECT 1 FROM public.user_profiles
     WHERE lower(username) = lower(base) AND id = p_uid
  ) THEN
    RETURN base;
  END IF;

  candidate := base;
  IF EXISTS (
    SELECT 1 FROM public.user_profiles WHERE lower(username) = lower(candidate)
  ) THEN
    candidate := base || '_' || substr(replace(p_uid::text, '-', ''), 1, 8);
  END IF;
  -- 短码也撞上的四十亿分之一：退到完整 uuid。username 是 varchar(255)，放得下。
  IF EXISTS (
    SELECT 1 FROM public.user_profiles WHERE lower(username) = lower(candidate)
  ) THEN
    candidate := base || '_' || replace(p_uid::text, '-', '');
  END IF;

  RETURN candidate;
END;
$$;

COMMENT ON FUNCTION public.unique_username(text, uuid) IS
  '把一个想要的用户名变成一个没人占用的（大小写不敏感）。空基名回退到 '
  'user_<uuid 短码>。注册的两条路径与改名接口共用这一份实现。';

-- SECURITY INVOKER（默认）：它只读 user_profiles、不写任何东西，调用方自己的
-- 权限说了算。浏览器不需要它 —— 改名走后端接口。
REVOKE EXECUTE ON FUNCTION public.unique_username(text, uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.unique_username(text, uuid) FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. 先把没有名字的账号补上，再上约束
-- ---------------------------------------------------------------------------
-- 顺序是重要的：索引和 NOT NULL 都会拒绝现有的坏数据，补在前面才不会卡住迁移。
UPDATE public.user_profiles p
   SET username = public.unique_username(
         NULLIF(split_part(COALESCE(u.email, ''), '@', 1), ''), p.id
       )
  FROM auth.users u
 WHERE u.id = p.id
   AND NULLIF(btrim(COALESCE(p.username, '')), '') IS NULL;

-- 没有对应 auth.users 行的孤儿 profile（理论上不该有）也得有名字。
UPDATE public.user_profiles
   SET username = public.unique_username(NULL, id)
 WHERE NULLIF(btrim(COALESCE(username, '')), '') IS NULL;

-- ---------------------------------------------------------------------------
-- 3. 全局唯一，且大小写不敏感
-- ---------------------------------------------------------------------------
-- 既有的 `user_profiles_username_key` 是大小写敏感的，所以 `iocrazy` 与
-- `IOCrazy` 可以并存 —— 对一个用来认人的名字，那是可冒充的。保留它（模型里
-- 声明着，且它本身没错），再加一条函数索引把大小写这一维也关上。
CREATE UNIQUE INDEX IF NOT EXISTS uniq_user_profiles_username_ci
  ON public.user_profiles (lower(username));

-- 「每个账号都有名字」变成结构性的，而不是「两条注册路径碰巧都填了」。
ALTER TABLE public.user_profiles
  ALTER COLUMN username SET NOT NULL;

COMMENT ON COLUMN public.user_profiles.username IS
  '主账号名。注册时自动生成，用户可改（PATCH /api/v1/auth/profile），全局唯一 '
  '且大小写不敏感。不是登录凭证 —— 登录走 Supabase Auth 的邮箱/手机号。';

-- ---------------------------------------------------------------------------
-- 4. 注册触发器改用那个函数
-- ---------------------------------------------------------------------------
-- mig 469 在函数体里内联了两段 IF EXISTS。现在那段逻辑有了唯一出处，且多了
-- 大小写不敏感这一条 —— 内联那份不知道新索引的存在，会让注册撞上它。
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
DECLARE
    uname TEXT;
BEGIN
    -- 想要的名字：显式给的 → 邮箱本地部分 → （都没有时由 unique_username
    -- 回退到 user_<uuid 短码>）。NULLIF 把 '' 折叠成「没有」。
    --
    -- ⚠️ `NEW.phone` 刻意不参与：用户名是给别人看的，而手机号是 PII 不是显示名；
    -- 而且 `ci_bootstrap.sql` 的 auth.users stub 没有这个列，引用它会让我们自己
    -- 的测试永远跑不到这段。手机号注册与匿名注册一样拿 user_<短码>，之后可以
    -- 自己改名。
    uname := public.unique_username(
        COALESCE(
            NULLIF(NEW.raw_user_meta_data->>'username', ''),
            NULLIF(split_part(COALESCE(NEW.email, ''), '@', 1), '')
        ),
        NEW.id
    );

    INSERT INTO public.user_profiles (id, username, role)
    VALUES (NEW.id, uname, 'user')
    ON CONFLICT (id) DO NOTHING;

    -- Auto-create personal team.
    -- kind='personal' is explicit so the column DEFAULT ('collaborative')
    -- does not take effect.
    -- Idempotent: uq_teams_owner_personal prevents a second personal team.
    INSERT INTO public.teams (name, owner_id, kind)
    VALUES (
        uname || '''s Workspace',
        NEW.id,
        'personal'
    )
    ON CONFLICT DO NOTHING;

    -- Hand them their initial tags (mig 468). Idempotent; ungrouped if the
    -- tag_groups rows are absent.
    PERFORM public.seed_initial_tags(NEW.id);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

NOTIFY pgrst, 'reload schema';
