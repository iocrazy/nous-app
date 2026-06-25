-- 317 — Nous models: persist last connectivity-test result.
--
-- The admin "Test" button ran a real probe (chat/embedding/asr) but the
-- green/red dot lived only in React state — it vanished on navigation and the
-- admin never saw WHEN a model was last checked. As a system-management view
-- this needs to survive page changes, so persist the last result on the row:
--   last_test_status  — 'ok' | 'fail' (NULL = never tested)
--   last_test_detail  — short human note ("chat ok", "2048 dims", error text)
--   last_tested_at    — when the probe ran
--
-- Written ONLY by the test endpoint (never by the generic update path), so it
-- does not perturb updated_at, which stays an edit timestamp.

ALTER TABLE public.nous_models
  ADD COLUMN IF NOT EXISTS last_test_status TEXT,
  ADD COLUMN IF NOT EXISTS last_test_detail TEXT,
  ADD COLUMN IF NOT EXISTS last_tested_at   TIMESTAMPTZ;

ALTER TABLE public.nous_models
  DROP CONSTRAINT IF EXISTS nous_models_last_test_status_check;
ALTER TABLE public.nous_models
  ADD CONSTRAINT nous_models_last_test_status_check
  CHECK (last_test_status IS NULL OR last_test_status IN ('ok', 'fail'));
