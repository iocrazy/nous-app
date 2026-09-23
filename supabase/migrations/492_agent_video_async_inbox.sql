-- 492: agent GenerateVideo 改为「提交、完成后回报」（异步）
-- 两个白名单各加一个值，其余原样保留（照 461 的 DROP/ADD 幂等写法，整表重声明）：
--   (a) 事件类型 media_job_done —— agent_video workflow 在**发起那条 run** 的
--       transcript 上记一笔「这个视频任务结束了」（成功或失败），run 可能早已结束；
--   (b) 收件箱 kind media_result —— 同一个结果以 agent_run_inbox 行交给模型，
--       issue 目标空闲时由 workflow body 的 deliver_or_dispatch 唤醒，
--       conversation 目标等用户下一条消息时在步边界被认领。
-- 不 SET ROLE（以连接角色 postgres 跑）；空库上安全（只动约束）。
BEGIN;

-- (a) 事件白名单 ---------------------------------------------------
ALTER TABLE public.agent_run_transcript_events
  DROP CONSTRAINT IF EXISTS agent_run_transcript_events_event_type_check;
ALTER TABLE public.agent_run_transcript_events
  ADD CONSTRAINT agent_run_transcript_events_event_type_check
  CHECK (event_type = ANY (ARRAY[
    'user'::text, 'assistant'::text, 'tool_call'::text, 'error'::text, 'system'::text,
    'llm_retry'::text, 'todo_write'::text,
    'compaction_start'::text, 'compaction_summary'::text, 'compaction_end'::text,
    'turn_end'::text,
    'step_start'::text, 'step_end'::text, 'inbox_claimed'::text,
    'deliverable'::text, 'budget_check'::text,
    'question_asked'::text, 'question_answered'::text,
    'capability_denied'::text,
    'fork'::text,
    'subagent_spawned'::text, 'subagent_done'::text, 'schedule_set'::text,
    'media_job_done'::text
  ]));
COMMENT ON CONSTRAINT agent_run_transcript_events_event_type_check
  ON public.agent_run_transcript_events IS
  'Allowed transcript event types. 436/443/453/459/460/461 as before. '
  '492: media_job_done (an async GenerateVideo job ended; written on the '
  'dispatching run, which may already have ended).';

-- (b) 收件箱 kind -------------------------------------------------
ALTER TABLE public.agent_run_inbox
  DROP CONSTRAINT IF EXISTS agent_run_inbox_kind_check;
ALTER TABLE public.agent_run_inbox
  ADD CONSTRAINT agent_run_inbox_kind_check
  CHECK (kind = ANY (ARRAY[
    'steer'::text, 'answer'::text, 'pause'::text, 'resume'::text,
    'budget_reply'::text,
    'subagent_result'::text,
    'media_result'::text
  ]));
COMMENT ON CONSTRAINT agent_run_inbox_kind_check ON public.agent_run_inbox IS
  '453: steer/answer/pause/resume/budget_reply. 461: subagent_result. '
  '492: media_result — an async GenerateVideo job hands its outcome back to '
  'the agent through the ONE queue.';

COMMIT;
