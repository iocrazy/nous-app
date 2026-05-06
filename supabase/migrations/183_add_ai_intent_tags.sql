-- 183_add_ai_intent_tags.sql
--
-- D9 first stab: replace the global "auto_transcribe / auto_summarize"
-- per-user toggle with intent-tags attached on the resource itself.
--
-- The old model fires every AI workflow on every download as long as
-- the user has the toggle on — wasteful on small / unrelated content
-- and surprises the user ("我没标 tag 不该跑 AI"). The new model: AI
-- workflows only fire when the user has explicitly attached one of the
-- intent tags below to the resource (either at parse time via the
-- single-link page tag picker, or post-download via the MediaCard
-- tag picker — the latter requires a follow-up endpoint, this
-- migration just seeds the catalog).
--
-- Tag naming mirrors the AI Library agent slugs / workflow names so
-- the dispatch helper can look up "tag.name → workflow" with no extra
-- mapping table.

INSERT INTO public.tags (id, name, name_zh, type, color, icon, enabled)
VALUES
  (generate_snowflake_id(), 'Transcript', '转录', 'system',
   '#6366f1', 'mic', true),
  (generate_snowflake_id(), 'Summary',    '总结', 'system',
   '#10b981', 'file-text', true),
  (generate_snowflake_id(), 'Analyze',    '分析', 'system',
   '#f59e0b', 'eye', true)
ON CONFLICT DO NOTHING;
