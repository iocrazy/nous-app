-- =============================================================
-- Performance: wrap bare auth.uid() / auth.role() in (SELECT ...)
-- inside every RLS policy. Supabase lint 0003 (auth_rls_initplan).
--
-- Without the SELECT wrapper, PostgreSQL re-invokes auth.uid() for
-- every row the query inspects. Wrapping it as a subquery makes the
-- planner hoist it into an initPlan — one call per query instead of
-- N calls per row.
-- =============================================================

DO $mig$
DECLARE
  r RECORD;
  new_qual TEXT;
  new_check TEXT;
  stmt TEXT;
  role_list TEXT;
  _cmd TEXT;
  changed INT := 0;
BEGIN
  FOR r IN
    SELECT schemaname, tablename, policyname, permissive, roles, p.cmd AS p_cmd, qual, with_check
    FROM pg_policies p
    WHERE schemaname = 'public'
      AND (
        qual ~ '\muid\(\)'
        OR qual ~ '\mrole\(\)'
        OR with_check ~ '\muid\(\)'
        OR with_check ~ '\mrole\(\)'
      )
  LOOP
    new_qual := r.qual;
    new_check := r.with_check;

    new_qual  := regexp_replace(new_qual,  '\muid\(\)',  '(SELECT auth.uid())',  'g');
    new_check := regexp_replace(new_check, '\muid\(\)',  '(SELECT auth.uid())',  'g');
    new_qual  := regexp_replace(new_qual,  '\mrole\(\)', '(SELECT auth.role())', 'g');
    new_check := regexp_replace(new_check, '\mrole\(\)', '(SELECT auth.role())', 'g');

    new_qual  := regexp_replace(new_qual,  '\(SELECT\s*\(SELECT\s+auth\.uid\(\)\)\)',  '(SELECT auth.uid())',  'g');
    new_check := regexp_replace(new_check, '\(SELECT\s*\(SELECT\s+auth\.uid\(\)\)\)',  '(SELECT auth.uid())',  'g');
    new_qual  := regexp_replace(new_qual,  '\(SELECT\s*\(SELECT\s+auth\.role\(\)\)\)', '(SELECT auth.role())', 'g');
    new_check := regexp_replace(new_check, '\(SELECT\s*\(SELECT\s+auth\.role\(\)\)\)', '(SELECT auth.role())', 'g');

    EXECUTE format('DROP POLICY %I ON %I.%I',
                   r.policyname, r.schemaname, r.tablename);

    role_list := array_to_string(r.roles, ', ');
    _cmd := CASE WHEN r.p_cmd IS NULL OR r.p_cmd = '' THEN 'ALL' ELSE r.p_cmd END;

    stmt := format('CREATE POLICY %I ON %I.%I AS %s FOR %s TO %s',
                   r.policyname, r.schemaname, r.tablename,
                   CASE WHEN r.permissive = 'PERMISSIVE' THEN 'PERMISSIVE' ELSE 'RESTRICTIVE' END,
                   _cmd, role_list);

    IF new_qual IS NOT NULL THEN
      stmt := stmt || format(' USING (%s)', new_qual);
    END IF;
    IF new_check IS NOT NULL THEN
      stmt := stmt || format(' WITH CHECK (%s)', new_check);
    END IF;

    EXECUTE stmt;
    changed := changed + 1;
  END LOOP;

  RAISE NOTICE 'Rewrote % policies', changed;
END
$mig$;
