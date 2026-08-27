-- 443: agent_run_transcript_events 放行 harness 二期的五个事件类型
--
-- 二期（docs/superpowers/plans/2026-08-25-harness-phase2-task-visibility.md）
-- 要把 agent 的工作进度落成持久事件（dsh 整值快照规则：推完整状态，绝不推
-- 裸 delta）。event_type 有 CHECK 白名单，一次加齐，避免逐波 churn（436 只加
-- llm_retry 的教训）：
--
--   todo_write          Skill(todo) 每次成功变更后的整表快照 {todos, counts}
--   compaction_start    压缩决定摘要后、调用前同步先落 {tier, tokens_before, window}
--   compaction_summary  摘要被接受后 {summary_tokens, head_tokens, attempts, path}
--   compaction_end      无论成败最后落 {tokens_after, tokens_saved, error?}
--                       —— start 先落、end 最后落：崩溃留下无配对 end 的孤儿
--                       start 可检测，绝不产出谎称完成的 end
--   turn_end            run_turn/stream_turn 每个出口 {reason, iterations, finish_reason?}
--
-- 为什么不复用 'error'：这些是生命周期事件不是错误，塞进 error 会让"这个 run
-- 有没有报错"的查询在每次成功的压缩/结束上亮灯（语义占坑）。
-- 命名沿用本表既有 snake_case（tool_call / llm_retry），不用 dsh 的斜杠形。
--
-- 幂等（DROP IF EXISTS 再 ADD）；纯放宽，现有行全部继续满足；无回填。
-- 不写 SET ROLE —— 以连接角色执行（见 CLAUDE.md 对应条目与 migration 365）。

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
    'llm_retry'::text,
    'todo_write'::text,
    'compaction_start'::text,
    'compaction_summary'::text,
    'compaction_end'::text,
    'turn_end'::text
  ]));

COMMENT ON CONSTRAINT agent_run_transcript_events_event_type_check
  ON public.agent_run_transcript_events IS
  'Allowed transcript event types. 436: llm_retry (same-model transient '
  'retry). 443: todo_write (whole-list todo snapshot), compaction_start/'
  'summary/end (lock bracket — an unmatched start is a detectable crash), '
  'turn_end (typed reason the turn stopped).';

COMMIT;
