-- 220_pipeline_tag_group.sql
--
-- Create the "Pipeline" tag group and assign the 3 AI-workflow trigger
-- tags (Transcript / Summary / Analyze) to it.
--
-- Why
-- ---
-- These three tags are system tags (type='system' since the seed
-- migration) and are hardcoded in backend/app/tasks/download_helpers.py
-- as the intent triggers for the AI workflows:
--   "Transcript" → ai_transcription_workflow
--   "Summary"    → chained via ai_transcription's success hook
--   "Analyze"    → maybe_chain_ai_pipeline (analyze_l1_workflow)
--
-- They were sitting in Uncategorized, lost among the 11 truly
-- uncategorized user tags. Grouping them under "Pipeline" gives them
-- a clear semantic home and visually separates "user-curated tags" from
-- "system-managed AI triggers".
--
-- Notes
-- -----
-- - Group name is the literal English "Pipeline" — matches the UI's
--   English-text convention (CLAUDE.md UI language rule).
-- - sort_order = 9 — after the 8 existing user groups, so it pins at
--   the bottom of the sidebar without disturbing the user's
--   curated order.
-- - The user-created "Workflow" tag (type='user', name_zh='工作流程')
--   is intentionally NOT moved; it's the user's own catch-all and
--   unrelated to the AI workflow engine.
-- - Backend behaviour does not depend on this group — tag matching in
--   download_helpers.py is by tag NAME, not group. Pure UI hygiene.

DO $$
DECLARE
  new_group_id BIGINT;
BEGIN
  -- Pick the next sort_order so we don't collide with existing groups
  -- on a fresh DB or partial seed.
  INSERT INTO public.tag_groups (name, sort_order)
  SELECT 'Pipeline', COALESCE(MAX(sort_order), 0) + 1 FROM public.tag_groups
  ON CONFLICT DO NOTHING
  RETURNING id INTO new_group_id;

  -- If the row already existed (idempotent re-run), look up its id.
  IF new_group_id IS NULL THEN
    SELECT id INTO new_group_id FROM public.tag_groups WHERE name = 'Pipeline' LIMIT 1;
  END IF;

  IF new_group_id IS NULL THEN
    RAISE EXCEPTION 'Pipeline tag_group could not be created or located';
  END IF;

  -- Move the 3 AI-workflow trigger system tags into the new group.
  -- Only system-type matches — a stray user tag named "Transcript" stays
  -- where it is.
  UPDATE public.tags
     SET group_id = new_group_id
   WHERE name IN ('Transcript', 'Summary', 'Analyze')
     AND type = 'system';
END $$;
