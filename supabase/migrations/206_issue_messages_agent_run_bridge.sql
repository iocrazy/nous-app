-- 206_issue_messages_agent_run_bridge.sql
--
-- Bridge: agent_runs lifecycle → issue_messages chat thread (A8.4).
--
-- When an agent_runs row resolves (status → completed | failed | cancelled)
-- AND it has issue_id set, emit a kind='agent_run' message into the issue's
-- chat thread automatically. This decouples the agent execution stack
-- (workforce / AgentRunner / DBOS) from the chat surface — once any code
-- path writes agent_runs.issue_id when starting an issue-scoped run, the
-- chat thread fills itself in with no further plumbing.
--
-- The kind='agent_run' message references agent_run_id (FK from mig 205),
-- so the chat row is uniquely tied to the run. Re-firing the trigger on
-- repeated UPDATEs is guarded by a duplicate-row check.

BEGIN;

CREATE OR REPLACE FUNCTION public.emit_agent_run_message_on_resolve()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  duration_s   INT;
  body_text    TEXT;
  has_message  BOOLEAN;
BEGIN
  -- Only fire when status moves into a terminal state.
  IF NEW.status NOT IN ('completed', 'failed', 'cancelled') THEN
    RETURN NEW;
  END IF;
  IF OLD.status = NEW.status THEN
    RETURN NEW;
  END IF;
  IF NEW.issue_id IS NULL THEN
    RETURN NEW;
  END IF;

  -- Don't double-emit if a chat row already exists for this run.
  SELECT EXISTS (
    SELECT 1 FROM public.issue_messages
    WHERE agent_run_id = NEW.id AND kind = 'agent_run'
  ) INTO has_message;
  IF has_message THEN
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

  INSERT INTO public.issue_messages (
    issue_id, kind, author_agent_id, agent_run_id,
    body, duration_seconds, meta
  ) VALUES (
    NEW.issue_id, 'agent_run', NEW.agent_id, NEW.id,
    body_text, duration_s,
    jsonb_build_object(
      'status', NEW.status,
      'cost_cents', NEW.cost_cents,
      'model', NEW.model,
      'prompt_tokens', NEW.prompt_tokens,
      'completion_tokens', NEW.completion_tokens,
      'error_code', NEW.error_code
    )
  );

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_agent_runs_emit_chat ON public.agent_runs;
CREATE TRIGGER trg_agent_runs_emit_chat
AFTER UPDATE OF status ON public.agent_runs
FOR EACH ROW
EXECUTE FUNCTION public.emit_agent_run_message_on_resolve();

COMMENT ON FUNCTION public.emit_agent_run_message_on_resolve() IS
  'Bridge from agent_runs lifecycle to issue_messages chat thread (A8.4).
   AFTER UPDATE OF status. When an agent_run with issue_id IS NOT NULL
   moves to a terminal state, insert a kind=agent_run row into the
   matching issue chat. Idempotent — checks for an existing row keyed
   on agent_run_id + kind to avoid double-emit on repeated updates.
   SECURITY DEFINER so it works regardless of which role triggered the
   agent_runs UPDATE (service_role from workforce, or user via API).';

COMMIT;
