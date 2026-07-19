-- 373: W3d — narrow notification inbox (per-recipient action-result feed).
--
-- WHY A NEW TABLE (not the existing `notifications`):
-- `public.notifications` + `public.user_notifications` (migration 009) are a
-- BROADCAST model — one row addressed to a team/system audience, with per-user
-- read-state living in a junction table. That surface still powers release-note
-- announcements (migration 254). The W3d inbox is a fundamentally different
-- data model: ONE row per recipient, its own read_at, a typed deep-link, and a
-- closed set of exactly three producer kinds. Overloading the broadcast table
-- would have meant bolting a user_id column + kind/severity/link onto a shape
-- built for the opposite fan-out. So this is a distinct table; the two coexist.
--
-- NARROWNESS (the design, not an accident): exactly three producer kinds ever
-- write here — generation_result, publish_result, autopilot_output. No comment
-- / @mention / status-change spam; Realtime already covers immediacy elsewhere.
-- The inbox is only "a result landed / needs my action".

CREATE TABLE IF NOT EXISTS public.inbox_notifications (
    id          BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id     UUID NOT NULL,                      -- recipient (auth.users.id)
    kind        TEXT NOT NULL
                CHECK (kind IN ('generation_result', 'publish_result', 'autopilot_output')),
    title       TEXT NOT NULL,
    body        TEXT,
    severity    TEXT NOT NULL DEFAULT 'info'
                CHECK (severity IN ('info', 'success', 'error')),
    -- Typed deep-link: a resolver on the frontend maps (link_kind, link_id) to a
    -- route. No raw URLs in the DB. link_id is text so it holds both snowflake
    -- ids (as strings, JS-precision-safe) and issue identifiers (e.g. "MH-42").
    link_kind   TEXT CHECK (link_kind IN ('issue', 'resource', 'publish_batch')),
    link_id     TEXT,
    team_id     BIGINT REFERENCES public.teams(id) ON DELETE CASCADE,
    read_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Inbox list query: recipient's rows, newest first.
CREATE INDEX IF NOT EXISTS idx_inbox_notifications_user_created
    ON public.inbox_notifications (user_id, created_at DESC);

-- Unread badge count: partial index over just the unread rows.
CREATE INDEX IF NOT EXISTS idx_inbox_notifications_user_unread
    ON public.inbox_notifications (user_id)
    WHERE read_at IS NULL;

-- Dedupe guard support: the notify() service checks for an identical
-- (user_id, kind, link_kind, link_id) row inside a 10-minute window before
-- inserting, to swallow double-fire seams. This index serves that lookup.
CREATE INDEX IF NOT EXISTS idx_inbox_notifications_dedupe
    ON public.inbox_notifications (user_id, kind, link_kind, link_id, created_at DESC);

-- ── Realtime ────────────────────────────────────────────────────────────────
-- Frontend InboxContext subscribes via Supabase Realtime (postgres_changes,
-- filter user_id=eq.<current user>) to live-update the badge + list. Idempotent
-- ADD TABLE (no-op if already a publication member). Mirrors migration 168.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename = 'inbox_notifications'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.inbox_notifications;
  END IF;
END
$$;

-- ── RLS ─────────────────────────────────────────────────────────────────────
-- The anon key reaches this table over Realtime, so recipient-only visibility
-- is enforced with RLS (mirrors task_tracking, migration 064). Reads are
-- SELECT-own; all writes (insert by producers, read_at updates by the router)
-- go through the backend engine under the service_role bypass.
ALTER TABLE public.inbox_notifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY inbox_notifications_select ON public.inbox_notifications
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY inbox_notifications_service_all ON public.inbox_notifications
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role'
    );

-- Reload PostgREST schema cache so the new table/policies are visible without a
-- restart (no SET ROLE — plain NOTIFY, per the migration conventions).
NOTIFY pgrst, 'reload schema';
