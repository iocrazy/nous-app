-- 405_issues_clear_stale_error_state.sql
--
-- One-off cleanup of stale `execution_state.error_code` / `error_message`
-- left behind by the pre-fix `issue_lifecycle.set_status`.
--
-- The old implementation only touched `execution_state` when an error or
-- outcome argument was passed, and when it did it ASSIGNED the whole column.
-- Two consequences:
--
--   * a `blocked` issue that later resumed (`in_progress`) or was parked
--     (`in_review`, via the "no declaration" branch that passes no arguments
--     at all) kept the error that stopped it, forever;
--   * any error/outcome write clobbered the keys owned by the other three
--     writers (`awaiting_input`, `turn`/`turn_started_at`, `stranded_*`).
--
-- `set_status` now always writes the column as a jsonb MERGE and owns the two
-- error keys explicitly — writing them when given an error, removing them
-- otherwise. This migration retro-applies that rule to rows written before the
-- fix; without it the historical rows never self-heal (nothing rewrites a
-- terminal issue).
--
-- Scope: every status EXCEPT 'blocked'. Under the new rule the error keys can
-- only survive on a row whose last transition actually wrote them, and that is
-- the blocked/errored path — so any non-blocked row carrying them is by
-- definition residue. Observed on the dev DB at authoring time: 2 `done` rows
-- and 1 `in_review` row, all carrying `error_code='execute_issue_failed'` from
-- a failure the issue had long since moved past.
--
-- Idempotent: the `?` guard makes a re-run a no-op, and jsonb `-` on an absent
-- key is a no-op anyway. Only touches the two keys — every other key in the
-- column is preserved (that is the whole point of the fix).

BEGIN;

-- `execution_state` is service_role-only: mig 170's issues_update_allowlist
-- trigger raises `insufficient_privilege` unless current_user is service_role
-- or supabase_admin, and every runner of this file connects as `postgres`
-- (run-migration.yml and the schema-drift gate both use `psql -U postgres`).
--
-- Suppress the trigger rather than switching role. `SET LOCAL ROLE
-- service_role` is the obvious move and it is what this migration did first —
-- it works against prod but FAILS the schema-drift gate with `permission
-- denied for table issues`, because supabase/ci_bootstrap.sql creates
-- service_role as a bare `CREATE ROLE ... NOLOGIN NOINHERIT` with no GRANTs
-- (on real Supabase the platform grants them, so the ephemeral DB has the
-- role's name but none of its privileges). Staying `postgres` and turning the
-- trigger off for this transaction needs no grants and behaves identically in
-- both environments.
--
-- Both settings are **LOCAL** deliberately: run-migration.yml concatenates
-- pending migrations into one batch file and feeds it to a single psql
-- session, so a session-scoped SET would leak into every migration that
-- follows in the batch. That is exactly how migration 176 silently failed to
-- drop a postgres-owned table for months (see 365's post-mortem). LOCAL
-- reverts at COMMIT.
--
-- Side effect, accepted: this also suppresses issues_touch_updated_at, so the
-- repaired rows keep their original updated_at. That is the desirable
-- behaviour for an internal data repair — it must not look like a user edit.
SET LOCAL session_replication_role = replica;

UPDATE public.issues
   SET execution_state = execution_state - 'error_code' - 'error_message'
 WHERE status <> 'blocked'
   AND execution_state IS NOT NULL
   AND (execution_state ? 'error_code' OR execution_state ? 'error_message');

COMMIT;
