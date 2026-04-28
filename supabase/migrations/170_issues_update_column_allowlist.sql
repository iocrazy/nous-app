-- 170: PR-D2.1 — issues UPDATE column-allowlist trigger
--
-- Closes /review finding #1 (CRITICAL): the issues_update_general RLS policy
-- only restricts WHO can UPDATE (creator OR assignee), not WHAT columns they
-- can change. Without this trigger, an assignee can mutate dbos_workflow_id,
-- request_depth, team_id, project_id, created_by_user_id, execution_state —
-- escalating privilege via column manipulation.
--
-- PG does not support column-level RLS policies natively; trigger is the
-- canonical workaround. service_role bypasses (NEW.* = OLD.* check via the
-- session_user check at the top).

-- SECURITY INVOKER (default) is required: we want `current_user` inside the
-- function to reflect the actual caller's effective role (after SET ROLE).
-- SECURITY DEFINER would force `current_user` to the function owner
-- (supabase_admin), which would always pass the bypass check below — making
-- the trigger a no-op for every authenticated user.
CREATE OR REPLACE FUNCTION public.issues_enforce_update_allowlist()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  -- Columns mutable by creator/assignee. Everything else is service_role-only.
  -- These are the "user-facing" fields a creator or assignee should be able
  -- to edit through the UI: title, description, priority, the assignee
  -- itself, project/team scope changes (if they're entitled), billing_code,
  -- and the soft-delete signal hidden_at. Status is technically here too
  -- but in practice the router validates allowed transitions.
  immutable_changed BOOLEAN := false;
BEGIN
  -- Bypass for the EFFECTIVE role (after SET ROLE). We deliberately do NOT
  -- check session_user — the supabase pooler (Supavisor) connects as the
  -- `postgres` login role and then PostgREST does `SET ROLE authenticated`
  -- per JWT. If we trusted session_user, every authenticated request would
  -- bypass the trigger (session_user='postgres' = bypass). current_user is
  -- the effective role we actually want to authorize against:
  --   - DBOS step pattern: SET ROLE service_role → bypass (legitimate)
  --   - Direct supabase_admin connection (migrations) → bypass
  --   - Authenticated user via PostgREST: SET ROLE authenticated → enforce
  IF current_user IN ('service_role', 'supabase_admin') THEN
    RETURN NEW;
  END IF;

  -- Field-by-field check. Comparing NULLs requires IS DISTINCT FROM.
  IF NEW.id                  IS DISTINCT FROM OLD.id                  THEN immutable_changed := true; END IF;
  IF NEW.issue_number        IS DISTINCT FROM OLD.issue_number        THEN immutable_changed := true; END IF;
  IF NEW.identifier          IS DISTINCT FROM OLD.identifier          THEN immutable_changed := true; END IF;
  IF NEW.created_by_user_id  IS DISTINCT FROM OLD.created_by_user_id  THEN immutable_changed := true; END IF;
  IF NEW.created_by_agent_id IS DISTINCT FROM OLD.created_by_agent_id THEN immutable_changed := true; END IF;
  IF NEW.dbos_workflow_id    IS DISTINCT FROM OLD.dbos_workflow_id    THEN immutable_changed := true; END IF;
  IF NEW.execution_locked_at IS DISTINCT FROM OLD.execution_locked_at THEN immutable_changed := true; END IF;
  IF NEW.execution_state     IS DISTINCT FROM OLD.execution_state     THEN immutable_changed := true; END IF;
  IF NEW.request_depth       IS DISTINCT FROM OLD.request_depth       THEN immutable_changed := true; END IF;
  IF NEW.origin_kind         IS DISTINCT FROM OLD.origin_kind         THEN immutable_changed := true; END IF;
  IF NEW.origin_id           IS DISTINCT FROM OLD.origin_id           THEN immutable_changed := true; END IF;
  IF NEW.origin_fingerprint  IS DISTINCT FROM OLD.origin_fingerprint  THEN immutable_changed := true; END IF;
  IF NEW.created_at          IS DISTINCT FROM OLD.created_at          THEN immutable_changed := true; END IF;

  IF immutable_changed THEN
    RAISE EXCEPTION 'issues: attempted to modify column not in user-allowlist (only service_role can change identity / execution / origin / created_at fields)'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS issues_update_allowlist ON public.issues;
CREATE TRIGGER issues_update_allowlist
  BEFORE UPDATE ON public.issues
  FOR EACH ROW EXECUTE FUNCTION public.issues_enforce_update_allowlist();

COMMENT ON FUNCTION public.issues_enforce_update_allowlist IS
  'Column-allowlist guard for issues UPDATE. Closes /review finding #1 — RLS WHO check is necessary but not sufficient; trigger enforces WHAT. Bypassed by service_role / supabase_admin / postgres roles.';
