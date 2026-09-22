-- 484: image generation moves entirely onto the user's own machine.
--
-- Decision (2026-09-22): the server-side halves are not wanted — everything
-- should run on the paired device. This migration does the IMAGE half only.
--
-- WHY VIDEO IS NOT IN HERE
-- ------------------------
-- Disabling `jimeng-cli-seedance` would break two workflows today.
-- `resolve_video_provider` hard-rejects any family other than "jimeng-cli":
--
--     if protocol is None or protocol.generation_family != "jimeng-cli":
--         raise RuntimeError("No video provider implementation for ...")
--
-- `canvas_generation` never reaches that code for a local row — it checks
-- `_local_engine` first and hands the job to the daemon. But
-- `script_shot_video.py` (分镜出视频) and `canvas_timeline.py` call
-- `resolve_video_provider` DIRECTLY, with no local branch at all. Retiring the
-- server-side video row turns both into
-- `RuntimeError: No video provider implementation for actual_provider='jimeng-local'`.
--
-- So the video row stays enabled until those two workflows learn the daemon
-- path. Tracked as the follow-up on this PR.
--
-- WHY DISABLE RATHER THAN DELETE
-- ------------------------------
-- Every consumer path filters on `is_enabled` first (db_registry._enabled_rows,
-- repo.list_enabled), so a disabled row is already invisible to dispatch.
-- Disabling makes this one switch to flip back; deleting makes it a rebuild.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Retire the two server-side IMAGE rows.
-- ---------------------------------------------------------------------------
-- Guarded on is_enabled so a re-run reports 0 instead of silently "succeeding"
-- against rows somebody has since turned back on deliberately.
DO $$
DECLARE
    disabled INT;
BEGIN
    UPDATE public.mediahub_models
    SET is_enabled = FALSE
    WHERE name IN ('codex-image', 'jimeng-cli-image')
      AND is_enabled = TRUE;
    GET DIAGNOSTICS disabled = ROW_COUNT;
    RAISE NOTICE '[484] server-side image rows disabled: %', disabled;
END $$;

-- ---------------------------------------------------------------------------
-- 2. The remaining Codex image row is just "GPT Image" now.
-- ---------------------------------------------------------------------------
-- "Codex" named the TRANSPORT (the codex CLI session), never the model — the
-- model is GPT Image either way. That qualifier only earned its place while a
-- server-side Codex row sat next to this one; with that row retired the
-- parenthetical distinguishes nothing and just invites the question this
-- rename answers ("is this the API one?" — no, the API rows are
-- openai-image-flare / -sunburst, and they say so).
UPDATE public.mediahub_models
SET display_name = 'GPT Image'
WHERE name = 'codex-local-image'
  AND display_name <> 'GPT Image';

COMMIT;

NOTIFY pgrst, 'reload schema';
