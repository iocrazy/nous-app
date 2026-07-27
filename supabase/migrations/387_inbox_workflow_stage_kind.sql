-- 387: Expand inbox_notifications kind CHECK constraint to include workflow_stage

ALTER TABLE public.inbox_notifications DROP CONSTRAINT IF EXISTS inbox_notifications_kind_check;
ALTER TABLE public.inbox_notifications ADD CONSTRAINT inbox_notifications_kind_check
  CHECK (kind IN ('generation_result', 'publish_result', 'autopilot_output', 'workflow_stage'));

NOTIFY pgrst, 'reload schema';
