-- 139_ai_library_phase1_fixes.sql
-- Correct skill_files column semantics, add agent_skills.enabled, add ai_sessions.agent_id FK,
-- cap slug column length per spec.
--
-- Context: Migration 138_ai_library_phase1.sql was applied and committed, but spec review
-- found deviations from the Phase 1 design doc. This migration fixes those deviations
-- without rewriting 138 (audit trail preserved).
--
-- Fixes:
--   1. skill_files: rename filename->path, allow NULL content, replace mime_type with
--      file_type + CHECK, add binary_url, replace unique index (skill_id, filename) with
--      (skill_id, path).
--   2. agent_skills: add missing `enabled` column.
--   3. ai_sessions: add FK ai_sessions.agent_id -> ai_agents(id) ON DELETE SET NULL.
--   4. Slug columns: narrow to VARCHAR(64) per spec (cosmetic fidelity).
--
-- RLS policies are NOT touched — reviewer confirmed they are correct.

BEGIN;

-- =============================================================================
-- 1. skill_files — rename columns + semantics + add missing column
-- =============================================================================

-- Rename filename -> path
ALTER TABLE skill_files RENAME COLUMN filename TO path;

-- Allow NULL content (binary-ref rows have no inline content)
ALTER TABLE skill_files ALTER COLUMN content DROP NOT NULL;

-- Replace mime_type with file_type + CHECK constraint
ALTER TABLE skill_files DROP COLUMN IF EXISTS mime_type;
ALTER TABLE skill_files ADD COLUMN IF NOT EXISTS file_type TEXT NOT NULL DEFAULT 'markdown'
  CHECK (file_type IN ('markdown', 'script', 'text-asset', 'binary-ref'));

-- Add binary_url for binary assets
ALTER TABLE skill_files ADD COLUMN IF NOT EXISTS binary_url TEXT;

-- Replace unique index on (skill_id, filename) with (skill_id, path)
DROP INDEX IF EXISTS ux_skill_files_filename;
CREATE UNIQUE INDEX IF NOT EXISTS ux_skill_files_path ON skill_files(skill_id, path);

COMMENT ON COLUMN skill_files.path IS 'Relative path within the skill bundle (e.g. SKILL.md, refs/example.md, assets/logo.png)';
COMMENT ON COLUMN skill_files.file_type IS 'markdown | script | text-asset | binary-ref';
COMMENT ON COLUMN skill_files.binary_url IS 'URL for binary-ref assets (content is NULL in that case)';

-- =============================================================================
-- 2. agent_skills — add missing `enabled` column
-- =============================================================================

ALTER TABLE agent_skills ADD COLUMN IF NOT EXISTS enabled BOOLEAN DEFAULT true;

COMMENT ON COLUMN agent_skills.enabled IS 'Per-binding toggle to disable a skill on an agent without removing the row';

-- =============================================================================
-- 3. ai_sessions.agent_id — add missing FK
-- =============================================================================

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ai_sessions_agent_id_fkey'
  ) THEN
    ALTER TABLE ai_sessions
      ADD CONSTRAINT ai_sessions_agent_id_fkey
      FOREIGN KEY (agent_id) REFERENCES ai_agents(id) ON DELETE SET NULL;
  END IF;
END $$;

-- =============================================================================
-- 4. Slug length cap (cosmetic — matches spec)
-- =============================================================================

ALTER TABLE ai_agents    ALTER COLUMN slug       TYPE VARCHAR(64);
ALTER TABLE skills       ALTER COLUMN slug       TYPE VARCHAR(64);
ALTER TABLE ai_sessions  ALTER COLUMN agent_slug TYPE VARCHAR(64);

COMMIT;
