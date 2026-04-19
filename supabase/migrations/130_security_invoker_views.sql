-- =============================================================
-- Switch `resource_statistics` and `daily_statistics` from
-- SECURITY DEFINER to SECURITY INVOKER so they respect the calling
-- user's RLS instead of executing with the creator's privileges.
--
-- Supabase lint 0010_security_definer_view: a DEFINER view can leak
-- global data to any caller who has SELECT on the view, bypassing
-- the underlying table's RLS. Switching to invoker aligns the view
-- with what callers would see if they queried the base table directly.
--
-- `author_statistics` is already invoker; leaving it alone.
-- =============================================================

ALTER VIEW public.resource_statistics SET (security_invoker = true);
ALTER VIEW public.daily_statistics    SET (security_invoker = true);
