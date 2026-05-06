-- 173: PR-D2.1 — issue_create_atomic stored procedure
--
-- Closes /review #PR-D2-deferred (atomic_create race window): the Python
-- `IssueRepository.atomic_create` runs (a) `RPC issue_next_identifier()`
-- and (b) `INSERT INTO issues` as TWO PostgREST round-trips. They are NOT
-- in the same PG transaction, so an INSERT failure (CHECK violation,
-- network blip, FK miss) leaves the counter advanced → permanent gap in
-- MH-N sequence.
--
-- Fix: wrap counter allocation + INSERT in a single SECURITY DEFINER
-- function. The Python repository switches to a single RPC call. Race
-- window collapses to PG-internal txn semantics (rollback-safe).
--
-- Caller pattern (Python repository):
--   row = await client.rpc("issue_create_atomic", {"payload": dict_payload}).execute()
--
-- The payload is a JSONB blob with ALL Issue insertable fields; the proc
-- merges the allocated identifier and issue_number, then inserts.

CREATE OR REPLACE FUNCTION public.issue_create_atomic(payload JSONB)
RETURNS public.issues
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  next_n  INTEGER;
  pfx     TEXT;
  ident   TEXT;
  new_row public.issues;
BEGIN
  -- Step 1: allocate identifier (atomic counter UPDATE)
  UPDATE public.issue_sequence
  SET counter = counter + 1
  WHERE scope = 'global'
  RETURNING counter, prefix INTO next_n, pfx;

  IF next_n IS NULL THEN
    RAISE EXCEPTION 'issue_sequence row missing for scope=global';
  END IF;

  ident := pfx || '-' || next_n::text;

  -- Step 2: INSERT using payload + allocated identifier. If this fails
  -- (CHECK violation, FK violation, RLS policy), the entire txn including
  -- the counter UPDATE rolls back — no gap.
  INSERT INTO public.issues (
    issue_number, identifier,
    team_id, project_id, parent_id,
    title, description,
    status, priority,
    assignee_agent_id, assignee_user_id,
    created_by_agent_id, created_by_user_id,
    dbos_workflow_id, execution_state,
    origin_kind, origin_id, origin_fingerprint,
    request_depth, billing_code
  ) VALUES (
    next_n, ident,
    (payload->>'team_id')::BIGINT,
    (payload->>'project_id')::BIGINT,
    (payload->>'parent_id')::BIGINT,
    payload->>'title',
    payload->>'description',
    COALESCE(payload->>'status', 'backlog'),
    COALESCE(payload->>'priority', 'medium'),
    (payload->>'assignee_agent_id')::UUID,
    (payload->>'assignee_user_id')::UUID,
    (payload->>'created_by_agent_id')::UUID,
    (payload->>'created_by_user_id')::UUID,
    payload->>'dbos_workflow_id',
    payload->'execution_state',
    COALESCE(payload->>'origin_kind', 'manual'),
    payload->>'origin_id',
    COALESCE(payload->>'origin_fingerprint', 'default'),
    COALESCE((payload->>'request_depth')::INTEGER, 0),
    payload->>'billing_code'
  )
  RETURNING * INTO new_row;

  RETURN new_row;
END;
$$;

REVOKE ALL ON FUNCTION public.issue_create_atomic(JSONB) FROM PUBLIC;
-- Granted to backend service roles only — end-user creates go through a
-- FastAPI handler that authenticates the user, then calls this via
-- service_role / mediahub_app. Direct grant to authenticated would let
-- a malicious user bypass the application-layer team/project validation
-- in handler code.
GRANT EXECUTE ON FUNCTION public.issue_create_atomic(JSONB)
  TO service_role, mediahub_app, mediahub_dbos;

COMMENT ON FUNCTION public.issue_create_atomic IS
  'Atomic issue creation: allocates MH-N identifier and inserts in a single PG transaction. Replaces the 2-round-trip pattern in Python IssueRepository.atomic_create. Closes the counter-gap race for INSERT-failure cases. PR-D2.1.';
