-- 379_beat_memos.sql — Beats redesign M5 (memo pins get their own table).
--
-- User verdict (twice): a beat-timeline memo and an inspiration-library note are
-- two different things. A memo is a laper-style node that lives IN the beats
-- arrangement, never in the library. M4 shoehorned memos into inspiration_notes
-- (anchor columns, mig 378); M5 gives them a dedicated table and cuts the tie.
--
-- One beat_memos row = a whole-second-anchored note on a script's Beats
-- timeline: free-form ``content`` plus up to four ``images`` (object-store path
-- strings under the ``beats/memos/`` prefix — the memo image serve route bounds
-- reads by memo id + index, so bare paths are safe to store).
--
-- script_id carries a real in-schema FK (ON DELETE CASCADE) — same shape as
-- script_beats (mig 350/372): dropping a script drops its memos. The composite
-- index backs the by-script memo-rail list query.
--
-- RLS: backend-only table. Every access path goes through FastAPI with the
-- service-role client (BeatMemoRepository); the frontend reaches it only via the
-- /api/v1/scripts/{id}/memos REST routes, never supabase-js. So the
-- service-role-only lockdown pattern (375_gallery_items / 377_beat_templates) is
-- the correct fix and keeps the table off the anon PostgREST surface. Ownership
-- is enforced in the router guard (verify_memo_access → script → team), not RLS.

BEGIN;

CREATE TABLE IF NOT EXISTS public.beat_memos (
    id         BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    script_id  BIGINT NOT NULL
        REFERENCES public.script_projects(id) ON DELETE CASCADE,
    anchor_sec INTEGER NOT NULL,                     -- whole-second timeline offset
    content    TEXT NOT NULL DEFAULT '',
    images     JSONB NOT NULL DEFAULT '[]'::jsonb,   -- ≤4 object-store path strings
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- List query: a script's memos ordered along the timeline.
CREATE INDEX IF NOT EXISTS idx_beat_memos_script
    ON public.beat_memos (script_id, anchor_sec);

ALTER TABLE public.beat_memos ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on beat_memos"
    ON public.beat_memos;
CREATE POLICY "Service role full access on beat_memos"
    ON public.beat_memos FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- Retire the M4 anchor tie on inspiration_notes. Zero rows were ever anchored
-- (the M4 publish path 422'd on an anchor_script_id type mismatch), so this is a
-- pure schema cleanup — no data migration. No SET ROLE (repo blood lesson).
DROP INDEX IF EXISTS public.idx_inspiration_notes_anchor;
ALTER TABLE public.inspiration_notes
    DROP COLUMN IF EXISTS anchor_script_id,
    DROP COLUMN IF EXISTS anchor_sec;

NOTIFY pgrst, 'reload schema';

COMMIT;
