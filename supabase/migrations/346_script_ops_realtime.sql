-- 346_script_ops_realtime.sql
--
-- Phase B / Phase 5 — live op streaming (C2).
--
-- Puts `script_ops` on the `supabase_realtime` publication so Supabase
-- Realtime delivers INSERT events to the frontend hook `useScriptOpsRealtime`.
-- The write path is unchanged: apply_element_ops still appends one row per
-- accepted op batch; collaborators now receive that row within ~300ms and
-- either splice it onto their optimistic state (op_seq === version+1, clean)
-- or refetch the scene (gap / dirty), reusing the 409-recovery path.
--
-- Three data-plane changes, all idempotent:
--   1. REPLICA IDENTITY FULL — script_ops is append-only, so INSERT payloads
--      already carry the full new row; FULL is set anyway to match the
--      canvases/task_tracking convention (mig 296 / 180) and stay correct if
--      the table ever grows an UPDATE path.
--   2. supabase_realtime membership — added via a pg_publication_tables guard
--      (ALTER PUBLICATION ... DROP TABLE has no IF EXISTS in any PostgreSQL
--      version, so we never DROP; we only ADD when absent — mig 296 idiom).
--   3. RLS SELECT for team members — postgres_changes inherits RLS for JWT
--      (authenticated) subscribers. script_ops previously had only a
--      service_role policy (mig 339), so authenticated clients received no
--      events. This adds a permissive SELECT gated on team membership via the
--      script_ops → script_scenes → script_projects (team_id direct) →
--      team_members join chain. Additive: the service_role FOR ALL policy is
--      untouched (permissive policies OR-combine).
--
--      The membership check runs inside a SECURITY DEFINER helper, NOT inline in
--      the policy: script_scenes itself has RLS restricted to service_role
--      (mig 339), so an inline subquery joining it evaluates under the caller's
--      RLS and returns zero rows for authenticated members (verified locally —
--      member saw 0). The definer function (owner = postgres) bypasses RLS on
--      the joined tables while auth.uid() still resolves to the CALLER, giving
--      the correct per-user visibility.

-- 1. Full row on the wire (idempotent).
ALTER TABLE script_ops REPLICA IDENTITY FULL;

-- 2. Membership helper (SECURITY DEFINER → bypasses RLS on the joined tables).
CREATE OR REPLACE FUNCTION public.can_read_script_op(p_scene_id bigint)
  RETURNS boolean
  LANGUAGE sql
  STABLE
  SECURITY DEFINER
  SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM script_scenes s
    JOIN script_projects sp ON sp.id = s.script_id
    JOIN team_members tm ON tm.team_id = sp.team_id
    WHERE s.id = p_scene_id
      AND tm.user_id = auth.uid()
  );
$$;

REVOKE ALL ON FUNCTION public.can_read_script_op(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.can_read_script_op(bigint) TO authenticated;

-- 3. RLS: authenticated team members may SELECT ops for scripts they can access.
DROP POLICY IF EXISTS script_ops_member_select ON script_ops;
CREATE POLICY script_ops_member_select ON script_ops
  FOR SELECT
  TO authenticated
  USING (public.can_read_script_op(scene_id));

-- 4. Add to the realtime publication only if not already a member. Guarded so a
-- fresh stack (supabase-realtime container not yet booted) skips gracefully.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    RAISE NOTICE 'supabase_realtime publication does not exist yet — skipping script_ops add. Re-run after supabase-realtime container has booted.';
    RETURN;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename = 'script_ops'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.script_ops;
    RAISE NOTICE 'script_ops added to supabase_realtime publication.';
  ELSE
    RAISE NOTICE 'script_ops already a supabase_realtime member — nothing to do.';
  END IF;
END
$$;

-- Refresh PostgREST's schema cache so the new policy takes effect immediately.
NOTIFY pgrst, 'reload schema';
