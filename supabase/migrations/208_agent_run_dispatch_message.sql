-- 208_agent_run_dispatch_message.sql
--
-- A8.6 — One agent_run = one chat row, evolving over its lifecycle.
--
-- mig 206 emitted an issue_messages row only when a run hit a terminal
-- state. That left a gap: the user posts a reply with an agent picked,
-- and nothing shows in chat until the agent finishes. That's a poor UX
-- for long-running runs.
--
-- This migration adds a second trigger that fires on agent_runs INSERT
-- (when issue_id is set) — emitting a placeholder chat row immediately
-- with body='Agent picking up…' and liveness_state='running'. Then the
-- existing terminal trigger from mig 206 is rewritten to UPDATE that
-- row in place when the run resolves, instead of inserting a duplicate.
--
-- Net result for the user:
--   t0  — clicks Send (with agent picked) → row appears: "Agent
--         picking up…" with green liveness dot
--   tN  — agent finishes → SAME row updates: real body + duration +
--         status pill flips
--   tDIE — scanner marks dead → SAME row updates: red pill + error
--
-- Frontend Realtime listener subscribes to both INSERT and UPDATE
-- events on issue_messages so the row mutates in place.

BEGIN;

-- ─────────────────────────────────────────────────────────────────
-- 1) Dispatch trigger: emit chat row on agent_runs INSERT
-- ─────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.emit_agent_run_dispatch_message()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF NEW.issue_id IS NULL THEN
    RETURN NEW;
  END IF;

  -- Idempotent: skip if a row for this run already exists (e.g. some
  -- code path inserted the chat row directly).
  IF EXISTS (
    SELECT 1 FROM public.issue_messages
    WHERE agent_run_id = NEW.id AND kind = 'agent_run'
  ) THEN
    RETURN NEW;
  END IF;

  INSERT INTO public.issue_messages (
    issue_id, kind, author_agent_id, agent_run_id,
    body, meta
  ) VALUES (
    NEW.issue_id, 'agent_run', NEW.agent_id, NEW.id,
    'Agent picking up…',
    jsonb_build_object(
      'status', NEW.status,
      'liveness_state', NEW.liveness_state,
      'model', NEW.model,
      'trigger', NEW.trigger
    )
  );

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_agent_runs_emit_dispatch ON public.agent_runs;
CREATE TRIGGER trg_agent_runs_emit_dispatch
AFTER INSERT ON public.agent_runs
FOR EACH ROW
EXECUTE FUNCTION public.emit_agent_run_dispatch_message();

-- ─────────────────────────────────────────────────────────────────
-- 2) Terminal trigger rewrite: UPDATE existing row instead of bail
-- ─────────────────────────────────────────────────────────────────
-- Replaces mig 206's emit_agent_run_message_on_resolve. New behaviour:
-- if a chat row already exists for this run (placed by the dispatch
-- trigger above, or an explicit insert), UPDATE it. Otherwise INSERT.
CREATE OR REPLACE FUNCTION public.emit_agent_run_message_on_resolve()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  duration_s   INT;
  body_text    TEXT;
  existing_id  UUID;
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
$$;

-- The trigger from mig 206 already binds; replacing the function picks
-- up the new logic without touching the trigger.
COMMENT ON FUNCTION public.emit_agent_run_message_on_resolve() IS
  'Bridge from agent_runs lifecycle to issue_messages chat thread.
   Updated by mig 208: now UPDATEs the placeholder row inserted by
   trg_agent_runs_emit_dispatch when the run resolves, falling back to
   INSERT only when no placeholder exists. One run = one chat row that
   evolves through the run''s lifecycle, instead of two separate rows.';

COMMENT ON FUNCTION public.emit_agent_run_dispatch_message() IS
  'AFTER INSERT trigger on agent_runs. When issue_id is set, emit a
   "Agent picking up…" placeholder chat row immediately so the user
   sees feedback before the agent runtime finishes. The terminal-state
   trigger (emit_agent_run_message_on_resolve) updates this same row
   in place when the run resolves.';

-- ─────────────────────────────────────────────────────────────────
-- 3) Liveness state-change → chat row UPDATE
-- ─────────────────────────────────────────────────────────────────
-- When the scanner promotes a run from running → silent → stuck (still
-- not terminal), update the chat row's meta so the UI pill flips colour
-- live. Without this, the user only sees state changes after the run
-- terminates.
CREATE OR REPLACE FUNCTION public.update_agent_run_message_on_liveness()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF NEW.liveness_state IS NOT DISTINCT FROM OLD.liveness_state THEN
    RETURN NEW;
  END IF;
  IF NEW.issue_id IS NULL THEN
    RETURN NEW;
  END IF;

  UPDATE public.issue_messages
     SET meta = meta || jsonb_build_object(
           'liveness_state', NEW.liveness_state,
           'continuation_attempt', NEW.continuation_attempt
         )
   WHERE agent_run_id = NEW.id AND kind = 'agent_run';

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_agent_runs_emit_liveness_change ON public.agent_runs;
CREATE TRIGGER trg_agent_runs_emit_liveness_change
AFTER UPDATE OF liveness_state ON public.agent_runs
FOR EACH ROW
EXECUTE FUNCTION public.update_agent_run_message_on_liveness();

COMMENT ON FUNCTION public.update_agent_run_message_on_liveness() IS
  'AFTER UPDATE OF liveness_state on agent_runs. Patches meta on the
   matching issue_messages row so the UI pill colour updates live as
   the scanner promotes running → silent → stuck without the run
   needing to terminate first.';

COMMIT;
