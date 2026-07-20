-- 382_ideation_topics.sql — Project Workflow M1.5 (Ideation topic pool).
--
-- Ideation is the global "before you make a project" surface (spec §1 / §9): a
-- topic = cover + title + reference, where the reference points (never copies)
-- at one of four sources — an inspiration note, an inspiration "# topic"
-- (hotspots feed), a download-library resource, or nothing (blank hand-written).
-- Status flows candidate → shortlisted → produced → archived. Creating a project
-- from a topic stamps projects.topic_id and best-effort marks the topic produced;
-- one topic may spawn many projects, so topic_id is a plain nullable BIGINT (no
-- unique constraint).
--
-- All four source ids are BIGINT snowflake (verified against the ORM models):
--   note_id             → inspiration_notes.id
--   inspiration_topic_id→ hotspots.id (the inspiration-library "# topic" feed)
--   resource_id         → resources.id
--   media_id            → parsed_media.id
-- We DON'T add DB foreign keys for these: the reference is a soft pointer whose
-- target may be trashed/deleted independently (the card just goes stale), and a
-- hard FK would block that. team_id / created_by carry the ownership contract.
--
-- RLS: topics is backend-only, reached through /api/v1/ideation/topics via the
-- service-role client (TopicsRepository); the frontend never touches it with
-- supabase-js. So the service-role-only lockdown (mirrors 380 workflow tables)
-- is the correct fix — ownership is enforced in the router's resolve_effective_role
-- guard, not in RLS.

BEGIN;

-- ── topics (team-scoped ideation pool) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.topics (
    id                   BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    team_id              BIGINT NOT NULL,
    title                TEXT NOT NULL,
    cover_url            TEXT,
    excerpt              TEXT,
    status               TEXT NOT NULL DEFAULT 'candidate',
    -- soft reference to exactly one source (or none = blank); not FK-constrained
    note_id              BIGINT,
    resource_id          BIGINT,
    media_id             BIGINT,
    inspiration_topic_id BIGINT,
    created_by           UUID,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT topics_status_check
        CHECK (status IN ('candidate', 'shortlisted', 'produced', 'archived'))
);
CREATE INDEX IF NOT EXISTS idx_topics_team_status
    ON public.topics (team_id, status);

-- ── projects.topic_id (source topic this project was created from) ──────────
ALTER TABLE public.projects ADD COLUMN IF NOT EXISTS topic_id BIGINT;

-- ── RLS: service-role-only lockdown (see header) ────────────────────────────
ALTER TABLE public.topics ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on topics" ON public.topics;
CREATE POLICY "Service role full access on topics"
    ON public.topics FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

NOTIFY pgrst, 'reload schema';

COMMIT;
