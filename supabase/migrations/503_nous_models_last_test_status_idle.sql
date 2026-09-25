-- Migration 503: last_test_status gains 'idle'; base qwen3 row gets its 32K window
--
-- 1. 'idle' — a local nous-engine model that is authorized but not loaded
-- ======================================================================
-- nous-engine loads models ON DEMAND, and one GPU card now carries three
-- interchangeable 27B variants (nous-qwen3-8-27b / -orcarouter / -huihui,
-- mig 502). The hourly probe used to POST /chat/completions (max_tokens=8) per
-- row, which forced the engine to load each variant in turn: the probe itself
-- was the load.
--
-- The probe now reads the engine's own readiness endpoint for
-- actual_provider='nous' llm / embedding / asr rows, which never loads anything:
--   GET {base_url}/models/{actual_model}
--     200     authorized and loaded          -> ok
--     503     authorized, not loaded         -> idle   (NOT a fault: the first
--             (engine ModelNotReadyError)               real request loads it)
--     404     key has no grant / no model    -> fail   (last_test_code model_not_found)
--     401/403 bad key                        -> fail   (last_test_code auth)
-- The admin "Test" button still performs a real call (it is supposed to load
-- the model and exercise it once), so only the hourly poll writes 'idle'.
--
-- Semantics next to the existing values (mig 428):
--   ok / fail   probed; reachable / failed (last_test_code says why)
--   idle        probed; authorized and healthy but cold. last_test_code NULL —
--               that column enumerates failure reasons and nothing failed.
--   not_probed  the probe has no protocol for the type; checked nothing.
--   NULL        never probed.
--
-- Code-side twins that must move with this CHECK:
--   backend/app/services/ai/nous_model_health.py  PROBE_STATUSES
--   backend/app/models/ai.py                      nous_models_last_test_status_check
-- (test_nous_probe_not_probed.py reads the ORM constraint back and compares.)
--
-- No backfill: nothing is 'idle' until the next hourly probe writes it, and a
-- row currently showing 'fail' because a probe could not load it will be
-- re-read by that probe within the hour.
--
-- 2. nous-qwen3-8-27b context window = 32768
-- ==========================================
-- Mig 502 left context_window_tokens NULL on the base row because the real vLLM
-- --max-model-len was unconfirmed. The user confirmed 32K on the nous-engine
-- admin page (2026-09-24), matching the -huihui sibling's 32768. Guarded by
-- IS NULL so an admin-set value survives, and a no-op on an empty database.
--
-- Idempotent: DROP ... IF EXISTS before ADD; the UPDATE's predicate is false
-- once applied. No SET ROLE (CLAUDE.md: it drops privileges in CI).

BEGIN;

ALTER TABLE public.nous_models
  DROP CONSTRAINT IF EXISTS nous_models_last_test_status_check;

ALTER TABLE public.nous_models
  ADD CONSTRAINT nous_models_last_test_status_check
  CHECK (
    last_test_status IS NULL
    OR last_test_status IN ('ok', 'fail', 'idle', 'not_probed')
  );

COMMENT ON COLUMN public.nous_models.last_test_status IS
  'Last connectivity-probe outcome: ok = reachable; fail = probed and failed (see last_test_code); idle = local nous-engine model authorized but not loaded right now (loads on first request) — NOT a fault; not_probed = the probe has no protocol for this model type and checked nothing — NOT a fault; NULL = never probed. Code-side twin: PROBE_STATUSES in backend/app/services/ai/nous_model_health.py.';

UPDATE public.nous_models
   SET context_window_tokens = 32768
 WHERE name = 'nous-qwen3-8-27b'
   AND context_window_tokens IS NULL;

NOTIFY pgrst, 'reload schema';

COMMIT;
