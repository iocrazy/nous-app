-- 399: needs_input 一等状态（spec 2026-07-30）— inbox 通知新增 agent_question kind。
-- 原 CHECK 是封闭枚举（mig 373 有意为之的 narrowness，后续 workflow_stage 已扩过一次）；
-- agent 提问是又一类"需要我行动"的收件箱事件，符合该表"a result landed / needs my action"的定位。
ALTER TABLE public.inbox_notifications
  DROP CONSTRAINT IF EXISTS inbox_notifications_kind_check;
ALTER TABLE public.inbox_notifications
  ADD CONSTRAINT inbox_notifications_kind_check
  CHECK (kind IN ('generation_result', 'publish_result', 'autopilot_output', 'workflow_stage', 'agent_question'));
