-- 199_seed_hash_idempotency.sql
--
-- Idempotency for backend/app/services/seed_loader.py.
--
-- Before: every backend startup PATCHed every agent + skill + skill_file
-- unconditionally, even when the source files on disk hadn't changed. With
-- ~5 agents × ~3 skills × multi-file content, this added 30-60s to cold
-- start. With uvicorn --reload (or watchexec) firing on every code edit,
-- that meant 30-60s of dead air on every save.
--
-- After: each row stores `seed_hash` = sha256(canonical content). On
-- restart the loader computes the hash for what's on disk, compares to
-- what's in the DB, and skips the PATCH when equal. Steady-state cost
-- becomes ~5 GETs (no writes) so cold start is sub-second.
--
-- Backfill is intentionally LEFT NULL — the next loader pass will fill
-- it in for entities that are present, and any hash mismatch (or NULL on
-- the DB side) just falls through to the existing PATCH path. So this
-- migration is safe to apply before deploying the seed_loader change.

ALTER TABLE ai_agents
    ADD COLUMN IF NOT EXISTS seed_hash text;

ALTER TABLE skills
    ADD COLUMN IF NOT EXISTS seed_hash text;

ALTER TABLE skill_files
    ADD COLUMN IF NOT EXISTS seed_hash text;

COMMENT ON COLUMN ai_agents.seed_hash IS
    'sha256 of IDENTITY.md + SOUL.md + AGENT.md + frontmatter; null = always re-upsert';
COMMENT ON COLUMN skills.seed_hash IS
    'sha256 of body_md + frontmatter_json; null = always re-upsert';
COMMENT ON COLUMN skill_files.seed_hash IS
    'sha256 of file content; null = always re-upsert';
