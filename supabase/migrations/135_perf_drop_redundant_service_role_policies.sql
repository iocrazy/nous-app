-- service_role in Supabase has rolbypassrls=true, so every RLS policy
-- that explicitly allows service_role is redundant — and actively
-- harmful: it becomes a second PERMISSIVE policy evaluated alongside
-- the real team/user policies for every authenticated query, causing
-- hundreds of multiple_permissive_policies lint warnings (0005).
--
-- Safe to drop: the base role keeps its BYPASSRLS privilege unchanged.

DO $mig$
DECLARE
  r RECORD;
  dropped INT := 0;
BEGIN
  FOR r IN
    SELECT schemaname, tablename, policyname
    FROM pg_policies
    WHERE schemaname = 'public'
      AND (
        qual LIKE '%service_role%'
        OR with_check LIKE '%service_role%'
      )
  LOOP
    EXECUTE format('DROP POLICY %I ON %I.%I', r.policyname, r.schemaname, r.tablename);
    dropped := dropped + 1;
  END LOOP;
  RAISE NOTICE 'Dropped % redundant service_role policies', dropped;
END
$mig$;
