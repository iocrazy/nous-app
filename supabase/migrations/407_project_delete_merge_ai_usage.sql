-- 407_project_delete_merge_ai_usage.sql
--
-- Fix: deleting a project that has ANY AI usage 500s with
--   duplicate key value violates unique constraint "ai_usage_hourly_dims_uq"
--
-- ROOT CAUSE
-- ----------
-- `ai_usage_hourly` is the hourly usage/cost rollup (measures: prompt_tokens,
-- completion_tokens, total_tokens, cached_input_tokens, cost_cents,
-- event_count) keyed by the dimension tuple
--   (bucket_hour, team_id, project_id, agent_id, model, module, attribution)
-- under a UNIQUE ... NULLS NOT DISTINCT constraint (ai_usage_hourly_dims_uq).
--
-- `project_id` carries `ON DELETE SET NULL`. So deleting a project nulls the
-- project_id of every one of its rollup rows. Because the unique key is
-- NULLS NOT DISTINCT, a nulled row is now considered identical to any
-- pre-existing NULL-project row sharing the other six dimensions — and the
-- referential SET NULL cannot merge, it just UPDATEs, so it trips the unique
-- constraint and the whole DELETE aborts. Any project that ever accrued a
-- single agent run becomes undeletable (500), while a project with no usage
-- deletes fine — which is why it went unnoticed.
--
-- FIX (preserve the existing "roll deleted-project usage into the NULL
-- bucket" intent, just make it actually work)
-- ----------------------------------------------------------------------
-- A BEFORE DELETE trigger on `projects` MERGES the project's rollup rows into
-- the NULL-project bucket — summing every measure via ON CONFLICT DO UPDATE —
-- then removes the source rows. It runs before the row is deleted, hence
-- before the FK's SET NULL fires, so SET NULL finds no rows left to null and
-- cannot collide. No usage or cost is lost: the totals move intact into the
-- NULL-project bucket, exactly what SET NULL was trying (and failing) to
-- express. `id` self-generates (generate_snowflake_id()); `total_tokens` is
-- GENERATED ALWAYS AS (prompt_tokens + completion_tokens), so it is omitted
-- from the write and re-derives from the summed components.
--
-- Scope note: among the tables that SET NULL on projects delete
-- (ai_usage_hourly, issues, skills), only ai_usage_hourly has project_id
-- inside a NULLS NOT DISTINCT unique key, so it is the only one that can
-- collide — issues/skills need no change.

CREATE OR REPLACE FUNCTION public.merge_project_ai_usage_to_null_bucket()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  -- total_tokens is GENERATED ALWAYS AS (prompt_tokens + completion_tokens):
  -- it must NOT appear in the column list — it re-derives itself from the
  -- summed components below.
  INSERT INTO public.ai_usage_hourly AS t (
    bucket_hour, team_id, project_id, agent_id, model, module, attribution,
    prompt_tokens, completion_tokens, cached_input_tokens,
    cost_cents, event_count, updated_at
  )
  SELECT bucket_hour, team_id, NULL::bigint, agent_id, model, module, attribution,
         prompt_tokens, completion_tokens, cached_input_tokens,
         cost_cents, event_count, now()
  FROM public.ai_usage_hourly
  WHERE project_id = OLD.id
  ON CONFLICT ON CONSTRAINT ai_usage_hourly_dims_uq DO UPDATE SET
    prompt_tokens       = t.prompt_tokens       + EXCLUDED.prompt_tokens,
    completion_tokens   = t.completion_tokens   + EXCLUDED.completion_tokens,
    cached_input_tokens = t.cached_input_tokens + EXCLUDED.cached_input_tokens,
    cost_cents          = t.cost_cents          + EXCLUDED.cost_cents,
    event_count         = t.event_count         + EXCLUDED.event_count,
    updated_at          = now();

  DELETE FROM public.ai_usage_hourly WHERE project_id = OLD.id;

  RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS trg_merge_project_ai_usage ON public.projects;
CREATE TRIGGER trg_merge_project_ai_usage
  BEFORE DELETE ON public.projects
  FOR EACH ROW
  EXECUTE FUNCTION public.merge_project_ai_usage_to_null_bucket();
