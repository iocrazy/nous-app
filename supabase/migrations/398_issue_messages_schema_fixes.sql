-- Migration 398: two latent issue_messages schema bugs exposed by #1658
--
-- PR #1658 added a forced FinishIssue declaration fallback
-- (forced_finish_declaration.py) that is the FIRST RunRecorder in production
-- to pass issue_id= at agent_runs INSERT time. That newly-reached code path
-- (dispatch trigger creates an issue_messages placeholder row up front, then
-- the resolve trigger tries to finalize it) exposed two bugs that every
-- pre-existing issue run silently avoided because it only ever backfilled
-- issue_id AFTER insert (no placeholder row -> resolve trigger always took
-- the INSERT branch, never the UPDATE branch below).
--
-- (a) emit_agent_run_message_on_resolve() declares existing_id UUID but
-- issue_messages.id is bigint (Snowflake, like every other PK in this repo
-- since mig 051). The UPDATE branch does:
--   SELECT id INTO existing_id FROM issue_messages WHERE agent_run_id = ...
-- which raises InvalidTextRepresentationError trying to cast a bigint into
-- a uuid variable. That exception aborts RunRecorder._finish() at the
-- agent_runs UPDATE, BEFORE record_usage() runs -- so every forced
-- declaration's tokens land in neither agent_runs nor ai_usage_hourly (an
-- unmetered API call), attribution is never written, and the run row is
-- stranded at heartbeat_lost for the stall reaper to clean up. One-line fix:
-- existing_id BIGINT. Logic is otherwise verbatim.
--
-- (b) issue_messages_author_agent_id_fkey is ON DELETE SET NULL, but
-- issue_messages_author_chk unconditionally requires author_agent_id IS NOT
-- NULL for kind='agent_run'. Deleting an ai_agents row that has ever
-- executed an issue tries to NULL that column on its agent_run cards, which
-- the CHECK forbids -> 500 on DELETE /ai-library/agents/{id}, permanently,
-- for any agent that has touched an issue.
--
-- Decision: relax the CHECK, don't CASCADE the FK. CASCADE was considered
-- (an agent_run card without its agent sounds like disposable history) but
-- author_agent_id is a single column shared across kind='comment' rows too
-- (an agent leaving a plain comment in the issue thread). CASCADE deletes
-- the whole issue_messages row, so it would also silently erase real
-- conversation history (agent comments) the day someone deletes that agent
-- -- not just the agent_run status cards. Tombstoning (author_agent_id ->
-- NULL, row kept) is the correct behavior for both kinds; the CHECK was
-- simply never written to allow it for agent_run because no code path had
-- ever created one before #1658.
--
-- Audited every other FK into ai_agents with ON DELETE SET NULL for the
-- same contradiction (FK allows NULL, CHECK forbids it): issues.assignee_
-- agent_id / issues.created_by_agent_id, agent_inbox.sender_agent_id,
-- agent_outbox.recipient_agent_id, ai_usage_hourly.agent_id, task_tracking.
-- agent_id. Only issues_creator_required comes close (created_by_agent_id
-- IS NOT NULL OR created_by_user_id IS NOT NULL) but it's an OR against a
-- second column, not an unconditional NOT NULL on the FK column itself, and
-- 0 rows currently have created_by_agent_id set with created_by_user_id
-- NULL. Not fixed here -- flagging as a watch item, not the same bug class.

BEGIN;

-- (a) existing_id UUID -> BIGINT. Everything else verbatim from the
-- pg_get_functiondef() dump of the live function (originally defined in
-- mig 206, meta_obj additions from later migrations preserved).
CREATE OR REPLACE FUNCTION public.emit_agent_run_message_on_resolve()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
  duration_s   INT;
  body_text    TEXT;
  existing_id  BIGINT;
  meta_obj     JSONB;
BEGIN
  IF NEW.status NOT IN ('completed', 'failed', 'cancelled') THEN
    RETURN NEW;
  END IF;
  IF OLD.status = NEW.status THEN
    RETURN NEW;
  END IF;
  IF NEW.issue_id IS NULL THEN
    RETURN NEW;
  END IF;

  duration_s := CASE
    WHEN NEW.ended_at IS NOT NULL AND NEW.started_at IS NOT NULL
    THEN GREATEST(0, EXTRACT(EPOCH FROM (NEW.ended_at - NEW.started_at))::INT)
    ELSE NULL
  END;

  body_text := COALESCE(
    NEW.output_summary,
    CASE NEW.status
      WHEN 'completed' THEN 'Run completed.'
      WHEN 'failed'    THEN COALESCE(NEW.error_message, 'Run failed.')
      WHEN 'cancelled' THEN 'Run cancelled.'
      ELSE NULL
    END
  );

  meta_obj := jsonb_build_object(
    'status', NEW.status,
    'liveness_state', NEW.liveness_state,
    'cost_cents', NEW.cost_cents,
    'model', NEW.model,
    'prompt_tokens', NEW.prompt_tokens,
    'completion_tokens', NEW.completion_tokens,
    'error_code', NEW.error_code,
    'continuation_attempt', NEW.continuation_attempt
  );

  -- Try UPDATE first (dispatch trigger placed a placeholder row).
  SELECT id INTO existing_id
  FROM public.issue_messages
  WHERE agent_run_id = NEW.id AND kind = 'agent_run'
  LIMIT 1;

  IF existing_id IS NOT NULL THEN
    UPDATE public.issue_messages
       SET body = body_text,
           duration_seconds = duration_s,
           meta = meta_obj
     WHERE id = existing_id;
  ELSE
    INSERT INTO public.issue_messages (
      issue_id, kind, author_agent_id, agent_run_id,
      body, duration_seconds, meta
    ) VALUES (
      NEW.issue_id, 'agent_run', NEW.agent_id, NEW.id,
      body_text, duration_s, meta_obj
    );
  END IF;

  RETURN NEW;
END;
$function$;

-- (b) Allow author_agent_id to be tombstoned (NULL) on kind='agent_run'
-- rows once the FK's ON DELETE SET NULL fires, instead of forbidding it.
ALTER TABLE public.issue_messages DROP CONSTRAINT IF EXISTS issue_messages_author_chk;
ALTER TABLE public.issue_messages ADD CONSTRAINT issue_messages_author_chk CHECK (
  CASE kind
    WHEN 'comment' THEN (author_user_id IS NOT NULL OR author_agent_id IS NOT NULL)
    WHEN 'agent_run' THEN true
    WHEN 'system_status' THEN (author_agent_id IS NULL)
    ELSE false
  END
);

COMMIT;
