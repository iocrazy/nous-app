-- 494: video generation moves entirely onto the user's own machine —
-- the VIDEO half that 484 deliberately left out.
--
-- 484 kept `jimeng-cli-seedance` enabled because three callers had no daemon
-- path and would raise on a local row. All three are now wired:
--   * script_shot_video / canvas_timeline / canvas empty-model → #2389
--     (db_registry.resolve_video_route picks the row once and branches to
--      local_dispatch for jimeng-local)
--   * the agent's GenerateVideo tool → #2398 (submit-only; the agent_video
--     workflow runs on the daemon and reports back through the inbox)
-- Production check (2026-09-23): `jimeng-local-video` is enabled and public;
-- no GENMEDIA_DEFAULT_VIDEO_* override is set in backend/worker env.
--
-- After this, the default video pick is the local row for every user
-- (it already was for everyone except the seedance row's private owner).
-- The server-side jimeng-cli pieces that are NOT this row (upscale, the
-- jimeng_cli router, admin auth) are untouched.
--
-- Disable, don't delete (same as 484): every consumer filters on
-- is_enabled, so this is one switch to flip back if needed.
-- Guarded on is_enabled so a re-run reports 0; safe on an empty DB.

DO $$
DECLARE
    disabled INT;
BEGIN
    UPDATE public.nous_models
    SET is_enabled = FALSE,
        updated_at = now()
    WHERE name = 'jimeng-cli-seedance'
      AND is_enabled = TRUE;
    GET DIAGNOSTICS disabled = ROW_COUNT;
    RAISE NOTICE '[494] server-side video row disabled: %', disabled;
END $$;
