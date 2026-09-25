-- Migration 504: share passwords become bcrypt hashes; the plain text goes away
--
-- Until now public.shares.password held the password in plain text. Anyone who
-- could read the table (a backup, a support query, a leaked dump) could open
-- every protected share. This migration:
--
--   1. adds shares.password_hash (bcrypt, cost 10 -- the same cost the backend
--      uses in app/services/library/share_passwords.py);
--   2. hashes every existing plain-text password into it, and overwrites the
--      plain-text column with a random lock value ('!locked:<uuid>');
--   3. adds a trigger that does the same to any plain text a legacy writer
--      still puts into shares.password.
--
-- Why a random lock instead of NULL, and why the trigger
-- ======================================================
-- run-migration.yml and deploy-gpu.yml run independently; either can finish
-- first, and deploy-gpu can roll back to the previous image after this ran.
-- The previous image reads shares.password and lets a visitor in when the
-- typed password equals it. With NULL there, it would treat every protected
-- share as unprotected (fail OPEN). With a random lock there, nobody can type
-- a matching value, so protected shares fail CLOSED until the new code is up.
-- The new code writes a lock too (share_password_columns), so a rollback
-- after new shares were created still fails closed. The trigger covers the
-- other direction: the old image creating a share writes plain text only, and
-- the trigger hashes it before the row is stored.
--
-- The column itself stays for now; dropping it is a follow-up once no image
-- that reads it can be rolled back to.
--
-- Visitor grants
-- ==============
-- The visitor grant (sg1.<id>.<expires>.<sig>, app/api/share_access.py) signs
-- a fingerprint of the password. It now fingerprints the hash, so every grant
-- issued before this migration stops verifying once the new code is up;
-- visitors re-enter the password once. Grants live 12 hours at most.
--
-- pgcrypto
-- ========
-- Production Supabase installs pgcrypto in the "extensions" schema; the CI
-- bootstrap installs it in "public". The schema is looked up rather than
-- assumed, and the trigger function pins its search_path to it.
--
-- Idempotent: rows that already have a hash are skipped; the function and
-- trigger are replaced.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE public.shares ADD COLUMN IF NOT EXISTS password_hash text;

COMMENT ON COLUMN public.shares.password_hash IS
  'bcrypt hash of the share password (mig 504). NULL = no password.';
COMMENT ON COLUMN public.shares.password IS
  'Legacy (mig 504): never the password. NULL or a random !locked:<uuid> value '
  'so that code which still compares against it fails closed. Read password_hash.';

DO $mig$
DECLARE
  crypto_schema text;
BEGIN
  SELECT n.nspname INTO crypto_schema
  FROM pg_extension e
  JOIN pg_namespace n ON n.oid = e.extnamespace
  WHERE e.extname = 'pgcrypto';

  IF crypto_schema IS NULL THEN
    RAISE EXCEPTION 'mig 504: pgcrypto is not installed';
  END IF;

  EXECUTE format($fn$
    CREATE OR REPLACE FUNCTION public.shares_hash_plaintext_password()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, %1$I
    AS $body$
    BEGIN
      -- A writer that set the password without setting the hash is a legacy
      -- writer handing us plain text. Code that knows about password_hash
      -- always writes both columns, so this never touches its rows.
      IF NEW.password IS NULL OR NEW.password LIKE '!locked:%%' THEN
        RETURN NEW;
      END IF;
      IF TG_OP = 'UPDATE'
         AND NEW.password_hash IS DISTINCT FROM OLD.password_hash THEN
        RETURN NEW;
      END IF;
      IF TG_OP = 'INSERT' AND NEW.password_hash IS NOT NULL THEN
        RETURN NEW;
      END IF;
      NEW.password_hash := crypt(NEW.password, gen_salt('bf', 10));
      NEW.password := '!locked:' || gen_random_uuid()::text;
      RETURN NEW;
    END;
    $body$
  $fn$, crypto_schema);

  -- Backfill. Rows that already carry a hash are left alone (re-runs).
  EXECUTE format($upd$
    UPDATE public.shares
    SET password_hash = %1$I.crypt(password, %1$I.gen_salt('bf', 10)),
        password = '!locked:' || gen_random_uuid()::text
    WHERE password IS NOT NULL
      AND password NOT LIKE '!locked:%%'
      AND password_hash IS NULL
  $upd$, crypto_schema);
END
$mig$;

-- A trigger function is not an RPC (PostgREST cannot call a function that
-- returns trigger), but it has no business being executable by anyone.
REVOKE EXECUTE ON FUNCTION public.shares_hash_plaintext_password()
  FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS shares_hash_plaintext_password ON public.shares;
CREATE TRIGGER shares_hash_plaintext_password
  BEFORE INSERT OR UPDATE OF password ON public.shares
  FOR EACH ROW EXECUTE FUNCTION public.shares_hash_plaintext_password();

COMMIT;
