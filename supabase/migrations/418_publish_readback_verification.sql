-- 418: publish read-back verification state (gap-closure P1-3)
--
-- Why these columns exist
-- =======================
-- `publish_task_accounts.published_url` / `platform_item_id` have been on the
-- table since migration 356 and NOTHING has ever written them on the session
-- channel: `douyin_publish._drive` deliberately returns both as NULL, because
-- Douyin's post-publish redirect lands on the content manager and carries no
-- identifier for the post that was just created (guessing the first card would
-- be wrong for any account with a scheduled or concurrently-published post).
--
-- P1-2 then closed the scheduled batch's issue on a TIMER: once
-- `publish_tasks.scheduled_at` passed, the mirror called it done. That is an
-- assumption dressed as a conclusion — the platform may still be reviewing it,
-- may have rejected it, may have had the schedule cancelled, or the user may
-- have deleted the post. P1-3 replaces the timer with a read-back: a browser
-- job opens the creator centre and confirms the post is actually live before
-- anything says "done".
--
-- These four columns are that read-back's state. They are BUSINESS columns on a
-- business table (route C: nothing here touches task_tracking's phase columns —
-- `publish_issue_mirror` stays a one-way reader).
--
--   verify_state      NULL   = never attempted / not applicable
--                     pending    = attempted, no conclusion yet (infra), retrying
--                     verified   = read back and the post IS live
--                     not_live   = read back and the post is NOT live (typed
--                                  reason in verify_detail) → issue goes blocked
--                     not_supported = platform has no read-back implementation
--                     abandoned  = retry budget spent without a conclusion →
--                                  issue goes blocked, because "we could not
--                                  confirm" must never render as "done"
--   verify_attempts   how many read-backs have been spent (retry cap)
--   verify_detail     typed, user-visible reason ('[under_review] ...')
--   verify_checked_at last attempt — drives the per-row cooldown so a batch
--                     cannot hammer the platform (风控克制)
--
-- No CHECK constraint on verify_state on purpose: the vocabulary lives in
-- `app/workflows/publish_readback.py` next to the code that branches on it, and
-- a DB-side enum would mean a migration every time a new typed reason is
-- learned from the live platform. The column is nullable and defaults to NULL,
-- so every existing row reads as "never attempted" — which is exactly true.

ALTER TABLE public.publish_task_accounts
    ADD COLUMN IF NOT EXISTS verify_state TEXT,
    ADD COLUMN IF NOT EXISTS verify_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS verify_detail TEXT,
    ADD COLUMN IF NOT EXISTS verify_checked_at TIMESTAMPTZ;

COMMENT ON COLUMN public.publish_task_accounts.verify_state IS
    'Read-back verdict: NULL=never attempted, pending, verified, not_live, not_supported, abandoned (P1-3).';
COMMENT ON COLUMN public.publish_task_accounts.verify_attempts IS
    'Read-back attempts spent. Bounded by PUBLISH_READBACK_MAX_ATTEMPTS; exhausting it means abandoned, not done.';
COMMENT ON COLUMN public.publish_task_accounts.verify_detail IS
    'Typed, user-visible reason for the verdict, e.g. ''[under_review] ...''.';
COMMENT ON COLUMN public.publish_task_accounts.verify_checked_at IS
    'Last read-back attempt. Drives the per-row cooldown between browser jobs.';

-- The sweep asks one question: "which successful session-channel rows still owe
-- a read-back?". Partial index because that set is a tiny minority of the table
-- and stays tiny (rows leave it as soon as they reach a terminal verdict).
CREATE INDEX IF NOT EXISTS idx_publish_task_accounts_readback_due
    ON public.publish_task_accounts (verify_checked_at NULLS FIRST)
    WHERE status = 'success'
      AND channel = 'session'
      AND (verify_state IS NULL OR verify_state = 'pending');
