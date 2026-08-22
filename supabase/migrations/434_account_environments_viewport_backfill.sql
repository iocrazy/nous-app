-- 434_account_environments_viewport_backfill.sql
--
-- 把 mig 424 漏掉的那一半补上：给已存在的 account_environments 行分配 viewport。
--
-- 为什么需要这条迁移
-- ==================
-- mig 424 的标题写着 backfill，它也确实回填了行 —— 但那条 INSERT 只写了
-- locale 与 timezone_id：
--
--     INSERT INTO public.account_environments (account_id, locale, timezone_id)
--     SELECT id, 'zh-CN', 'Asia/Shanghai' ...
--
-- 而 viewport 恰恰是它自己那份文档里唯一"能逐账号不同又不引入自相矛盾"的轴
-- （user_agent 改不动 Client Hints、locale/timezone 逐账号不同反而是异常、
-- geo 一写就变成不弹窗直接给坐标）。于是 424 之后的实际状态是：
--
--   * 每个已绑账号都有一行 env
--   * 每一行的 viewport 都是 NULL
--   * `pin_environment` 是 ON CONFLICT DO NOTHING，**这些行永远不会再拿到值**
--
-- 三个生产账号实测（2026-08-21）全部命中这个状态。也就是说：唯一有效的隔离轴
-- 被认真设计出来了，然后对 100% 的账号是关着的 —— 而且不会有任何报错。
--
-- 候选表来自哪里
-- ==============
-- 下面 VALUES 里的六组尺寸**逐字来自**
-- `backend/app/services/distribution/account_environment.py::SESSION_VIEWPORTS`，
-- 顺序也一致。两处各写一份是这条迁移无法避免的（SQL 调不到 Python），所以有
-- `test_viewport_backfill_matches_python` 读这个文件并断言两边相同 —— 漂移会
-- 让测试红，而不是让某个账号拿到一个不存在的屏幕分辨率。
--
-- ⚠️ 每个候选都必须是**真实存在且常见的桌面分辨率**：Playwright 设了 viewport
-- 之后会把 `screen` 也覆盖成同样的值，所以这个数会同时作为"屏幕分辨率"被读到。
--
-- 分配规则与 Python 侧的差别（刻意的）
-- ====================================
-- `choose_viewport` 是**随机**挑一个同 scope 未被占用的；这条迁移是**确定性**的
-- （按 scope 内绑定顺序轮转）。差别是有意的：
--
--   * 随机适合"新账号绑定时，它不知道别人是谁"；
--   * 回填面对的是一批已知账号，确定性让这条迁移可复现、可预期、可测。
--
-- 两者的**保证是同一条**：同 scope + 同平台下，最多 6 个账号互不相同；超过 6 个
-- 才开始重复（重复远好于全部相同）。
--
-- 幂等
-- ====
-- WHERE 只命中 viewport 为空的行，所以重跑无害；**且永远不会改动一个已经有值的
-- 行** —— 那正是 `pin_environment` 拒绝提供 update 入口的理由：一个每次登录指纹
-- 都在变的账号，比一个指纹固定的账号更可疑。这条迁移补的是"从来没有过值"，不是
-- "换一个值"。

BEGIN;

WITH targets AS (
  SELECT
    ae.account_id,
    sa.scope_type,
    sa.scope_id,
    sa.platform,
    -- scope 内的稳定序号。它同时是下面的轮转偏移量，这就是同一条 UPDATE 里
    -- 两个目标行不会挑到同一个候选的原因 —— LATERAL 看到的是语句开始时的快照，
    -- 彼此看不见对方刚写的值，所以不能靠 NOT EXISTS 去互斥。
    row_number() OVER (
      PARTITION BY sa.scope_type, sa.scope_id, sa.platform
      ORDER BY sa.created_at, sa.id
    ) AS n
  FROM public.account_environments ae
  JOIN public.social_accounts sa ON sa.id = ae.account_id
  WHERE ae.viewport_width IS NULL
     OR ae.viewport_height IS NULL
),
picked AS (
  SELECT t.account_id, c.w, c.h
  FROM targets t
  CROSS JOIN LATERAL (
    SELECT v.idx, v.w, v.h
    FROM (VALUES
      (1, 1366, 768),    -- 经典入门笔记本
      (2, 1280, 800),    -- 16:10 小尺寸笔记本（宽度等于下界）
      (3, 1440, 900),    -- MacBook Air / 16:10
      (4, 1536, 864),    -- 1920x1080 @125% 缩放后的 CSS 像素
      (5, 1600, 900),    -- 16:9 笔记本
      (6, 1680, 1050)    -- WSXGA+
    ) AS v(idx, w, h)
    -- 让开同 scope 里**已经**钉住的尺寸（当前生产没有这种行，但一旦有，
    -- 回填就不该把新账号撞到老账号身上）。
    WHERE NOT EXISTS (
      SELECT 1
      FROM public.account_environments sib
      JOIN public.social_accounts sa2 ON sa2.id = sib.account_id
      WHERE sa2.scope_type = t.scope_type
        AND sa2.scope_id = t.scope_id
        AND sa2.platform = t.platform
        AND sib.viewport_width = v.w
        AND sib.viewport_height = v.h
    )
    -- 轮转：第 n 个目标从第 n 个候选开始挑，于是同一 scope 的目标彼此错开。
    ORDER BY ((v.idx + t.n) % 6), v.idx
    LIMIT 1
  ) c
)
UPDATE public.account_environments ae
   SET viewport_width = picked.w,
       viewport_height = picked.h
  FROM picked
 WHERE ae.account_id = picked.account_id;

COMMIT;
