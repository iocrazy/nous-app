-- 439 — daemon environment report (IC-style in-page CLI detection).
--
-- The daemon's preflight already knows the local codex version/path, whether
-- gpt-image-2-skill is installed and whether a login exists — but it only
-- printed to the terminal. Persist the report per device so the settings
-- page can show it (IC's 检测 CLI panel is the reference UX).

ALTER TABLE public.codex_daemons
    ADD COLUMN IF NOT EXISTS env_report JSONB;

COMMENT ON COLUMN public.codex_daemons.env_report IS
    'Latest daemon-reported local environment: {codex_version, codex_ok, '
    'skill_ok, auth_ok, node_version, reported_at}. NULL until first connect.';
