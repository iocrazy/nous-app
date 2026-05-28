-- supabase/migrations/235_storage_path_remap.sql
-- Spec 1 PR-D — storage path remap (DB half).
--
-- Updates resources.file_path + resource_versions.file_path: replaces the
-- user_uuid embedded in the path (teams/{user_uuid}/...) with the matching
-- personal_team snowflake (teams/{snowflake}/...).
--
-- File-system half is operator-only:
--   scripts/migrations/2026-05-28-storage-path-personal-team.sh
-- The operator runs the script (sudo mv on the NAS) AND merges this PR in
-- the same maintenance window so CI applies this migration immediately
-- after. The window where DB and disk disagree should be < ~1 minute and
-- only affects the resources belonging to one user.
--
-- Pre-conditions:
--   - PR-A (mig 227-230) shipped: every personal user has a personal team.
--   - PR-C (mig 234) shipped: scope_id already remapped.
--
-- Idempotency: rows whose path no longer matches the LIKE clause are
-- skipped — safe to re-apply.
--
-- See docs/superpowers/specs/2026-05-28-id-unification-design.md § 5.

BEGIN;

WITH mapping AS (
    SELECT owner_id::text AS user_uuid, id::text AS team_snowflake
      FROM public.teams
     WHERE kind = 'personal'
)
UPDATE public.resources r
   SET file_path = REPLACE(file_path,
                            'teams/' || m.user_uuid || '/',
                            'teams/' || m.team_snowflake || '/')
  FROM mapping m
 WHERE r.file_path LIKE 'teams/' || m.user_uuid || '/%';

WITH mapping AS (
    SELECT owner_id::text AS user_uuid, id::text AS team_snowflake
      FROM public.teams
     WHERE kind = 'personal'
)
UPDATE public.resource_versions v
   SET file_path = REPLACE(file_path,
                            'teams/' || m.user_uuid || '/',
                            'teams/' || m.team_snowflake || '/')
  FROM mapping m
 WHERE v.file_path LIKE 'teams/' || m.user_uuid || '/%';

-- Sanity assertion: no UUID-style prefixes should remain after this point.
-- If the operator missed the matching mv, the next deploy still leaves DB
-- in a consistent state for everyone whose dir was renamed; users whose
-- file_path still embeds a UUID would already have been broken before this
-- ran (no personal team mapping → not remapped here).
DO $$
DECLARE
    leftover_resources int;
    leftover_versions int;
BEGIN
    SELECT COUNT(*) INTO leftover_resources
      FROM public.resources r
      JOIN public.teams t ON t.kind = 'personal' AND t.owner_id::text = SPLIT_PART(r.file_path, '/', 2)
     WHERE r.file_path LIKE 'teams/%';
    SELECT COUNT(*) INTO leftover_versions
      FROM public.resource_versions v
      JOIN public.teams t ON t.kind = 'personal' AND t.owner_id::text = SPLIT_PART(v.file_path, '/', 2)
     WHERE v.file_path LIKE 'teams/%';

    IF leftover_resources > 0 OR leftover_versions > 0 THEN
        RAISE EXCEPTION 'storage path remap left % resources / % resource_versions still pointing at user_uuid prefix',
                        leftover_resources, leftover_versions;
    END IF;
END $$;

COMMIT;
