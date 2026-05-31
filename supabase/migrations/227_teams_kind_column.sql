-- supabase/migrations/227_teams_kind_column.sql
-- Adds `kind` column to teams + unique-personal-team-per-owner constraint.
-- See docs/superpowers/specs/2026-05-28-id-unification-design.md § 3.

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'collaborative'
    CHECK (kind IN ('personal', 'collaborative'));

COMMENT ON COLUMN public.teams.kind IS
    'personal = auto-created single-member team; collaborative = user-created multi-member team. '
    'See ID-unification design 2026-05-28.';

CREATE UNIQUE INDEX IF NOT EXISTS uq_teams_owner_personal
    ON public.teams (owner_id) WHERE kind = 'personal';
