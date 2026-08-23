-- 436: agent_run_transcript_events 允许 'llm_retry' 事件类型
--
-- 背景（harness 落地计划 W1）：重试次数目前只活在 LLMRetryMiddleware 的
-- 内存循环变量里（`for attempt in range(...)`）。进程一重启就归零，UI 无从
-- 知道"这一轮正在第几次重试、还要等多久"，事后也无法回答"这次失败之前
-- 试过几次"。把每次重试落成持久事件即可解决全部三件。
--
-- 为什么需要迁移：event_type 有 CHECK 白名单，当前只允许
-- user / assistant / tool_call / error / system。
--
-- 为什么不复用 'error'：重试是生命周期事件，不是错误。塞进 'error' 会让
-- 任何"这个 run 有没有报错"的查询在每次成功的重试上亮灯 —— 正是本仓反复
-- 吃亏的那类语义漂移（把两件不同的事挤进同一个取值，之后谁都分不开）。
--
-- 为什么不叫 'llm/retry'（dsh 的写法）：本表既有取值都是无分隔符的
-- snake_case（`tool_call`），混入斜杠会让"事件类型长什么样"变成两套约定。
--
-- 幂等：重复执行安全（先 DROP 再 ADD）。
-- 纯放宽：现有行全部继续满足新约束，不需要回填，也没有数据迁移。
-- 无需 SET ROLE —— 以连接角色执行（CI 里是 postgres，即 owner）。
--   参见 migration 365 的结论与 CLAUDE.md「迁移里不要写 SET ROLE」。

BEGIN;

ALTER TABLE public.agent_run_transcript_events
  DROP CONSTRAINT IF EXISTS agent_run_transcript_events_event_type_check;

ALTER TABLE public.agent_run_transcript_events
  ADD CONSTRAINT agent_run_transcript_events_event_type_check
  CHECK (event_type = ANY (ARRAY[
    'user'::text,
    'assistant'::text,
    'tool_call'::text,
    'error'::text,
    'system'::text,
    'llm_retry'::text
  ]));

COMMENT ON CONSTRAINT agent_run_transcript_events_event_type_check
  ON public.agent_run_transcript_events IS
  'Allowed transcript event types. llm_retry (436) records one same-model '
  'transient retry attempt — provider, policy_key, attempt/max, delay_ms, '
  'failure summary — so the count survives a process restart and the UI can '
  'show retry progress. NOT a cross-model fallback: that remains prohibited.';

COMMIT;
