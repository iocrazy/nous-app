-- scripts/migrations/2026-05-28-storage-path-personal-team.sql
-- Companion to 2026-05-28-storage-path-personal-team.sh.
--
-- Updates resources.file_path + resource_versions.file_path: replaces the
-- user_uuid embedded in the path (teams/{user_uuid}/...) with the matching
-- personal_team snowflake (teams/{snowflake}/...).
--
-- Pre-condition: PR-A (mig 227-230) + PR-C (mig 234) have shipped — every
-- personal user has a personal team and that team's snowflake is known.
--
-- Idempotent: rows whose file_path no longer matches the LIKE clause are
-- skipped.
--
-- See docs/superpowers/specs/2026-05-28-id-unification-design.md § 5.

BEGIN;

-- Build mapping CTE once, reuse across tables
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

-- Verification queries (informational — not asserted at SQL level;
-- the calling shell script asserts via its own SELECT after COMMIT).
SELECT
  'after_update' AS phase,
  (SELECT COUNT(*) FROM public.resources
    WHERE file_path ~ 'teams/[0-9a-f]{8}-[0-9a-f]{4}') AS resources_uuid_remaining,
  (SELECT COUNT(*) FROM public.resource_versions
    WHERE file_path ~ 'teams/[0-9a-f]{8}-[0-9a-f]{4}') AS versions_uuid_remaining;

COMMIT;
