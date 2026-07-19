-- 377_beat_templates.sql — Beats redesign M3.5 (user custom templates).
--
-- A user-owned, reusable beat-sheet template: a name plus an ordered list of
-- percentage anchors. Unlike the three built-in methodologies (which live in
-- frontend code as i18n role keys), a custom template stores each beat's own
-- title / summary / color verbatim inside the `anchors` JSONB, so a hand-made
-- sheet reproduces exactly when re-applied against any target runtime.
--
-- anchors shape: [{ "title": str, "summary": str|null, "pctStart": num,
--                   "pctEnd": num, "color": "#rrggbb"|null }, ...]
--
-- RLS: backend-only table. Every access path goes through FastAPI with the
-- service-role client (BeatTemplateRepository); the frontend reaches it only
-- via the /api/v1/beat-templates REST routes, never supabase-js. So the
-- service-role-only lockdown pattern (375_gallery_items.sql) is the correct
-- fix and keeps the table off the anon PostgREST surface. Ownership is enforced
-- in the router guard (verify_beat_template_access), not RLS.

BEGIN;

CREATE TABLE IF NOT EXISTS public.beat_templates (
    id         BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id    UUID NOT NULL,                 -- owner (auth.users.id)
    name       VARCHAR(100) NOT NULL,
    anchors    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- List query: the owner's templates, newest first.
CREATE INDEX IF NOT EXISTS idx_beat_templates_user_created
    ON public.beat_templates (user_id, created_at DESC);

ALTER TABLE public.beat_templates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on beat_templates"
    ON public.beat_templates;
CREATE POLICY "Service role full access on beat_templates"
    ON public.beat_templates FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

NOTIFY pgrst, 'reload schema';

COMMIT;
