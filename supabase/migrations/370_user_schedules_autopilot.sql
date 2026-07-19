-- 370_user_schedules_autopilot.sql
--
-- W2a: Autopilot hardening for scheduled agent routines.
--
-- Adds the columns the master scheduler (app/workflows/scheduled_master.py)
-- needs to run schedules unattended without turning a worker restart or a
-- slow/broken routine into a flood or a silent runaway:
--
--   * timezone            — IANA tz name the cron_expr is interpreted in. Cron
--                           hours are wall-clock in this zone; next_fire_at is
--                           still STORED as UTC. Lets "0 9 * * *" mean "9am
--                           local" and survive DST instead of drifting.
--   * consecutive_fails   — run of dispatch failures with no success in
--                           between. Reset to 0 on a successful fire; NOT
--                           touched by skips (stale discard / skip_if_active).
--                           At the auto-pause threshold the scheduler flips
--                           enabled=false so a permanently broken routine stops
--                           firing every minute.
--   * paused_at           — when the scheduler auto-paused the row (NULL = not
--                           auto-paused). Distinguishes an operator/user disable
--                           (enabled=false, paused_at NULL) from an auto-pause.
--   * pause_reason        — human-readable reason shown in the Routines UI.
--   * skipped_count       — fires discarded as stale or gated by delivery
--                           policy. Telemetry only; excluded from failure rate.
--   * stale_after_minutes — a due fire older than this many minutes past its
--                           next_fire_at is discarded (skipped) instead of
--                           dispatched. Prevents worker-restart backfill floods
--                           AND a 9am daily run firing at 8pm after downtime.
--
-- Schema-drift gate: the SQLAlchemy model (backend/app/models/storyboard.py
-- UserSchedules) is updated in the same PR — CI reflects the real DB and
-- diffs it against the models, so a missing model column fails red.

ALTER TABLE public.user_schedules
    ADD COLUMN IF NOT EXISTS timezone            TEXT        NOT NULL DEFAULT 'UTC',
    ADD COLUMN IF NOT EXISTS consecutive_fails   INT         NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS paused_at           TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS pause_reason        TEXT,
    ADD COLUMN IF NOT EXISTS skipped_count       INT         NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS stale_after_minutes INT         NOT NULL DEFAULT 60;

COMMENT ON COLUMN public.user_schedules.timezone IS
    'IANA timezone name (e.g. "America/New_York") the cron_expr is interpreted
     in. Cron fields are wall-clock in this zone; next_fire_at is stored as UTC.
     Invalid names fall back to UTC at compute time.';
COMMENT ON COLUMN public.user_schedules.consecutive_fails IS
    'Run of dispatch failures since the last success. Reset to 0 on a
     successful fire; NOT changed by skipped fires (stale discard /
     skip_if_active gate). Drives auto-pause.';
COMMENT ON COLUMN public.user_schedules.paused_at IS
    'When the master scheduler auto-paused this row after too many consecutive
     failures (NULL = not auto-paused). An operator/user disable leaves this
     NULL; only the scheduler sets it.';
COMMENT ON COLUMN public.user_schedules.pause_reason IS
    'Human-readable reason for an auto-pause, surfaced in the Routines UI.';
COMMENT ON COLUMN public.user_schedules.skipped_count IS
    'Count of fires discarded as stale or gated by delivery policy. Telemetry
     only — excluded from the failure rate that drives auto-pause.';
COMMENT ON COLUMN public.user_schedules.stale_after_minutes IS
    'A due fire more than this many minutes past its next_fire_at is discarded
     (counted in skipped_count) instead of dispatched. Guards against
     worker-restart backfill floods and stale wall-clock runs.';

-- PostgREST schema cache refresh (new columns must be visible to the API).
NOTIFY pgrst, 'reload schema';
