-- Migration 403: Distribution — 'session' as a publish channel (S1)
--
-- publish_task_accounts.channel recorded which of the two OAuth-era routes an
-- account's row took ('official' = server-side open API, 'h5' = schema URL the
-- user confirms on their phone). Mig 401 adds a third binding kind, so the
-- per-account result row needs a third channel value.
-- See docs/superpowers/specs/2026-08-04-distribution-session-channel-design.md §3.3.
--
-- status is deliberately NOT touched: the session channel walks the plain
-- pending → publishing → success/failed path and never produces 'pending_share'
-- (that state exists only because H5 hands off to a human phone).
--
-- 356 declared channel's CHECK inline on the column, so Postgres named it
-- publish_task_accounts_channel_check. Drop-then-add is the only way to widen
-- it. The column is VARCHAR(10) and 'session' is 7 chars — no type change.
--
-- Idempotent: DROP CONSTRAINT IF EXISTS before ADD CONSTRAINT; re-apply is a
-- no-op.

BEGIN;

ALTER TABLE public.publish_task_accounts DROP CONSTRAINT IF EXISTS publish_task_accounts_channel_check;
ALTER TABLE public.publish_task_accounts ADD CONSTRAINT publish_task_accounts_channel_check
  CHECK (channel IN ('official', 'h5', 'session'));

COMMENT ON COLUMN public.publish_task_accounts.channel IS
  'Publish route for this account: official (open API), h5 (schema URL, user confirms on phone), or session (browser automation, mig 403).';

NOTIFY pgrst, 'reload schema';

COMMIT;
