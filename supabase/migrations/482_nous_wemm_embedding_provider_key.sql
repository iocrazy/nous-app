-- 482: the replacement embedding row landed on the vendor key too.
--
-- Migration 480 moved the three nous-engine rows off `openai`. While that PR
-- was in flight the embedding row was swapped by hand in Admin → AI Models —
-- `nous-qwen3-embedding-8b` out, `nous-wemm-embedding-4b` in — and the new row
-- was created under `openai`, because at that moment the dropdown had no
-- `nous` option to pick.
--
-- So this is not 480 having missed a row: it is the same mistake recurring
-- through the only path that could still produce it. The durable half of the
-- fix ships alongside this file (the admin Edit dialog can now change a row's
-- actual_provider, which it never could before — the PUT body always accepted
-- the field, the form simply never offered it). This migration cleans up the
-- one row that already exists.
--
-- Guarded on the current value so it cannot fight a manual correction: if the
-- row has already been moved by hand, this is a no-op rather than a surprise.

BEGIN;

DO $$
DECLARE
    moved INT;
BEGIN
    UPDATE public.mediahub_models
    SET actual_provider = 'nous'
    WHERE name = 'nous-wemm-embedding-4b'
      AND actual_provider = 'openai';
    GET DIAGNOSTICS moved = ROW_COUNT;
    RAISE NOTICE '[482] nous-wemm-embedding-4b moved openai -> nous: %', moved;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
