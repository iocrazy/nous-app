-- 411_fix_resource_ai_rls_join.sql
-- 083 建的两条 RLS 策略 join 错列:resource_summaries.resource_id 与
-- resource_transcripts.resource_id 存的都是 resources.id,策略却拿去比
-- resources.media_id → 谓词恒 false(2026-08-07 生产 pg_policy 实查确认)。
-- 当前潜伏:后端走 service_role 绕过 RLS;一旦前端直连 Supabase 读这两表,
-- 属主将看不到自己的任何摘要/转写。
-- 修法:join 改回主键列。保留生产已有的 (SELECT auth.uid()) initplan 形式。

BEGIN;

DROP POLICY IF EXISTS "Manage summaries of owned resources" ON resource_summaries;
CREATE POLICY "Manage summaries of owned resources" ON resource_summaries
  FOR ALL
  USING (EXISTS (
    SELECT 1 FROM resources
    WHERE resources.id = resource_summaries.resource_id
      AND resources.creator_id = (SELECT auth.uid())
  ));

DROP POLICY IF EXISTS "Manage transcripts of owned resources" ON resource_transcripts;
CREATE POLICY "Manage transcripts of owned resources" ON resource_transcripts
  FOR ALL
  USING (EXISTS (
    SELECT 1 FROM resources
    WHERE resources.id = resource_transcripts.resource_id
      AND resources.creator_id = (SELECT auth.uid())
  ));

COMMIT;
