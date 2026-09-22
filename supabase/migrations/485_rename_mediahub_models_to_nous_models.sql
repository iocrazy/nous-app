-- 485: rename `mediahub_models` back to `nous_models`.
--
-- This reverts migration 334, and the reason 334 gave is now inverted. 334
-- said: "The 'nous' name was confusing (read as an external product); it is a
-- first-class mediahub table." Since then the product itself became nous —
-- nous.ink, nous-app, nous-backend, nous-engine — and the mediahub line was
-- retired (2026-07-25). So "mediahub" is now the name that reads as something
-- external, and this table is a first-class **nous** table.
--
-- Pure rename. No column, constraint semantics, policy or data change.
--
-- ⚠️ WHY THE COMPATIBILITY VIEW
-- ----------------------------
-- `run-migration.yml` and `deploy-gpu.yml` fire independently on the same merge
-- commit — CLAUDE.md records this as a known gap ("migration 与代码部署无顺序
-- 保证"). Migration-first is the overwhelmingly likely order (a psql run beats a
-- docker build), and in that window the OLD backend is still asking for
-- `mediahub_models`. That table is on the path of EVERY AI call
-- (`resolve_nous_model` resolves the row before any provider is built), so a
-- bare rename would take the whole AI surface down for the length of a build.
--
-- The view removes that window: old code keeps reading and writing through
-- `mediahub_models` (a single-table view over `nous_models` is auto-updatable,
-- so INSERT/UPDATE/DELETE pass through), new code uses the real table.
--
-- `security_invoker = true` matters: without it the view would run as its owner
-- and silently bypass the base table's RLS. Migration 481 just closed a leak on
-- this exact table; a view that re-opened it would be the same bug wearing a
-- different hat.
--
-- Grants mirror 481's end state deliberately — service_role only, NOT anon /
-- authenticated. The browser has no business reading this table through either
-- name.
--
-- The view is dropped in a follow-up once this deploy has settled. It is not
-- load-bearing beyond that window.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Table
-- ---------------------------------------------------------------------------
-- NOT `ALTER TABLE IF EXISTS public.mediahub_models RENAME TO nous_models`.
-- After step 4 below, `mediahub_models` exists again — as the compatibility
-- VIEW — so on a second run `IF EXISTS` matches the view and tries to rename
-- IT, failing with `relation "nous_models" already exists` and leaving the
-- transaction aborted. Caught by re-running this file against an already
-- migrated database.
--
-- The guard is therefore two-sided: rename only when the target does not yet
-- exist AND the source is a real table (`relkind = 'r'`), never the view.
DO $$
BEGIN
    IF to_regclass('public.nous_models') IS NULL
       AND EXISTS (
           SELECT 1 FROM pg_class
            WHERE relname = 'mediahub_models'
              AND relnamespace = 'public'::regnamespace
              AND relkind = 'r'
       )
    THEN
        ALTER TABLE public.mediahub_models RENAME TO nous_models;
        RAISE NOTICE '[485] mediahub_models -> nous_models';
    ELSE
        RAISE NOTICE '[485] rename skipped (already applied, or fresh database)';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Constraints — names read back from production 2026-09-22, not guessed.
-- ---------------------------------------------------------------------------
-- Guarded per name so a partially-applied run (or a fresh database that never
-- carried the mediahub spelling) is a no-op rather than an error.
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('mediahub_models_pkey',                   'nous_models_pkey'),
            ('mediahub_models_name_key',               'nous_models_name_key'),
            ('mediahub_models_type_check',             'nous_models_type_check'),
            ('mediahub_models_pricing_type_check',     'nous_models_pricing_type_check'),
            ('mediahub_models_last_test_status_check', 'nous_models_last_test_status_check'),
            ('mediahub_models_last_test_code_check',   'nous_models_last_test_code_check'),
            ('mediahub_models_owner_user_id_fkey',     'nous_models_owner_user_id_fkey')
        ) AS t(old_name, new_name)
    LOOP
        IF EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = 'public.nous_models'::regclass
               AND conname = r.old_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE public.nous_models RENAME CONSTRAINT %I TO %I',
                r.old_name, r.new_name
            );
        END IF;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- 3. Standalone index (the pkey/unique indexes follow their constraints).
-- ---------------------------------------------------------------------------
ALTER INDEX IF EXISTS public.idx_mediahub_models_type
    RENAME TO idx_nous_models_type;

-- ---------------------------------------------------------------------------
-- 4. Compatibility view for the deploy window.
-- ---------------------------------------------------------------------------
-- RLS policies were never renamed by 334 — production still carries
-- `nous_models_select` / `nous_models_admin_*`, and they follow the table, so
-- there is nothing to rename here. Verified against pg_policies 2026-09-22.
CREATE OR REPLACE VIEW public.mediahub_models
    WITH (security_invoker = true)
    AS SELECT * FROM public.nous_models;

COMMENT ON VIEW public.mediahub_models IS
    'DEPRECATED compatibility shim for migration 485. Exists only so a backend '
    'deployed before this migration keeps working. Drop once 485 has shipped.';

REVOKE ALL ON public.mediahub_models FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.mediahub_models TO service_role;

COMMIT;

NOTIFY pgrst, 'reload schema';
