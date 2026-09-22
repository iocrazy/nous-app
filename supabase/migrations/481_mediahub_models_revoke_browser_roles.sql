-- 481: close the second door on public.mediahub_models.
--
-- THE LEAK
-- --------
-- Measured 2026-09-21 against production: the publishable key baked into the
-- browser bundle (frontend/.env.production) reads the catalog straight off
-- PostgREST —
--
--   GET https://sb.nous.ink/rest/v1/mediahub_models?select=name,base_url,...
--   → 200, every enabled row, including base_url = http://10.0.0.10:8000/v1
--
-- The backend has always been careful here: `_PUBLIC_COLS` in
-- mediahub_model_repository.py is a hand-maintained allowlist that deliberately
-- withholds `api_key`, `base_url` and `description` ("admin-internal ops notes
-- (e.g. private ZeroTier IPs, BYOK source references) that must never surface
-- in the user-facing platform-models card").
--
-- That allowlist only governs /api/v1. The RLS policy (migration 431) is
-- ROW-level — "enabled rows are visible to everyone" — and says nothing about
-- columns, so PostgREST hands out every column of every row it admits. Same
-- shape as CLAUDE.md's SECURITY DEFINER lesson: a second path to the same data
-- that no test walks, so the careful projection on path one proves nothing.
--
-- THE FIX
-- -------
-- Nothing in the browser needs this table directly. Every consumer — the model
-- picker, canvas generation capabilities, the admin page — goes through the
-- backend, which connects as `postgres`/`service_role` over a direct SQL
-- session and is unaffected by role grants. Verified by grepping the whole repo
-- for `mediahub_models` outside migrations: frontend/ and admin/ contain only
-- comments, never a `.from('mediahub_models')`.
--
-- RLS is left exactly as it is. It is no longer the only thing standing
-- between an anon key and base_url, but it is still worth having: if a future
-- `GRANT` ever restores table access, the row filter is still in place.

BEGIN;

REVOKE ALL ON TABLE public.mediahub_models FROM anon, authenticated;

-- Supabase's default privileges grant the browser roles access to every new
-- table in `public`, so future-proof this one too: without it, the next
-- `ALTER DEFAULT PRIVILEGES` sweep or a restore-from-dump quietly reopens the
-- door and nothing says a word.
--
-- NOT revoked from service_role / postgres: the backend is that role, and it
-- is the only thing that legitimately reads api_key and base_url.
DO $$
BEGIN
    RAISE NOTICE '[481] anon SELECT on mediahub_models: %',
        has_table_privilege('anon', 'public.mediahub_models', 'SELECT');
    RAISE NOTICE '[481] authenticated SELECT on mediahub_models: %',
        has_table_privilege('authenticated', 'public.mediahub_models', 'SELECT');
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
