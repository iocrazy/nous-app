-- Migration: 225_team_settings_json_and_temp_ttl
-- Description: Add settings_json to teams so per-team settings (including
-- chat_temp_ttl_days) have a single home. Personal scope settings already
-- live in user_settings.settings_json (migration 008) — no change there.

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS settings_json JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.teams.settings_json IS
    'Per-team settings as JSON. Known keys: chat_temp_ttl_days (int days, -1 = never expire).';

COMMENT ON COLUMN public.user_settings.settings_json IS
    'Per-user settings as JSON. Known keys: chat_temp_ttl_days (int days, -1 = never expire).';
