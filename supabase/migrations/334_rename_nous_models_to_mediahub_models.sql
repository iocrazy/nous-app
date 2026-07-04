-- Migration 334: rename table `nous_models` → `mediahub_models`
--
-- Pure rename (no schema/logic change). The table stores admin-configured
-- platform AI models. The "nous" name was confusing (read as an external
-- product); it is a first-class mediahub table. Data is preserved verbatim by
-- ALTER ... RENAME. Constraints / indexes / RLS policies are renamed to match
-- the ORM (`MediahubModels.__table_args__` now declares `mediahub_models_*`).
--
-- NOT touched (different concepts / persisted data — out of scope):
--   * `point_transactions.is_nous` + `idx_point_transactions_is_nous` (billing flag)
--   * `nous.user_enabled` / `ai_module.*.nous_allowed` (governance gate keys)
--   * `nous:` / `nous-` value prefixes in user_settings (model-pick data format)
--   * nous-center provider (`provider_slug` starting `nous/`)
--
-- Idempotent: guarded so re-apply is a no-op.

BEGIN;

-- 1. Table
ALTER TABLE IF EXISTS public.nous_models RENAME TO mediahub_models;

-- 2. Constraints (rename only if the old name still exists on the table)
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT old_name, new_name FROM (VALUES
      ('nous_models_pkey',                   'mediahub_models_pkey'),
      ('nous_models_name_key',               'mediahub_models_name_key'),
      ('nous_models_pricing_type_check',     'mediahub_models_pricing_type_check'),
      ('nous_models_type_check',             'mediahub_models_type_check'),
      ('nous_models_last_test_status_check', 'mediahub_models_last_test_status_check')
    ) AS t(old_name, new_name)
  LOOP
    IF EXISTS (
      SELECT 1 FROM pg_constraint c
      JOIN pg_class rel ON rel.oid = c.conrelid
      JOIN pg_namespace n ON n.oid = rel.relnamespace
      WHERE n.nspname = 'public' AND rel.relname = 'mediahub_models'
        AND c.conname = r.old_name
    ) THEN
      EXECUTE format(
        'ALTER TABLE public.mediahub_models RENAME CONSTRAINT %I TO %I',
        r.old_name, r.new_name
      );
    END IF;
  END LOOP;
END $$;

-- 3. Index
ALTER INDEX IF EXISTS public.idx_nous_models_type RENAME TO idx_mediahub_models_type;

-- 4. RLS policies (rename only if old policy name still present)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'mediahub_models'
      AND policyname = 'nous_models_admin_all'
  ) THEN
    ALTER POLICY nous_models_admin_all ON public.mediahub_models
      RENAME TO mediahub_models_admin_all;
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'mediahub_models'
      AND policyname = 'nous_models_public_read'
  ) THEN
    ALTER POLICY nous_models_public_read ON public.mediahub_models
      RENAME TO mediahub_models_public_read;
  END IF;
END $$;

COMMIT;

-- 5. Refresh PostgREST schema cache so the API sees the new table name
--    (CI otherwise serves the stale `nous_models` relation until next reload).
NOTIFY pgrst, 'reload schema';
