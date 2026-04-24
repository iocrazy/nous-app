-- Migration 151: cascade skill_files mutations to skills.updated_at
--
-- Background
-- ----------
-- prompt_composer.py computes the system-message cache fingerprint over
-- ``agent.updated_at`` + ``skills.updated_at``. When a user edits a sub-
-- file (e.g. references/api.md) through the /skill-files endpoints, only
-- the skill_files row's ``updated_at`` advances — the parent ``skills``
-- row is untouched. The composer then emits the **old** fingerprint and
-- any upstream prompt cache (Anthropic, vLLM KV-cache) happily serves
-- stale reasoning.
--
-- Fix is at DB level so every write path (seed_loader, REST endpoints,
-- admin manual edits, future migrations) participates automatically and
-- the application layer doesn't need to remember to bump.
--
-- Trigger fires AFTER INSERT/UPDATE/DELETE on skill_files and bumps the
-- matching ``skills.updated_at`` to now(). COALESCE(NEW, OLD) handles
-- all three event types with one function.

CREATE OR REPLACE FUNCTION skill_files_bump_parent_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  UPDATE skills
     SET updated_at = now()
   WHERE id = COALESCE(NEW.skill_id, OLD.skill_id);
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_skill_files_bump_parent ON skill_files;

CREATE TRIGGER trg_skill_files_bump_parent
AFTER INSERT OR UPDATE OR DELETE ON skill_files
FOR EACH ROW
EXECUTE FUNCTION skill_files_bump_parent_updated_at();

COMMENT ON FUNCTION skill_files_bump_parent_updated_at IS
  'Keeps skills.updated_at in sync with skill_files mutations so that '
  'prompt_composer cache fingerprints correctly invalidate when any '
  'sub-file (references/, scripts/, assets/) changes.';
