-- Wave 5d (M2.B): consolidation columns for agent_memories.
--
-- consolidation_level: how many merge passes this row has been through.
--   0 = original (most rows)
--   1 = merged from 2+ siblings
--   2 = merged from level-1 supers
-- superseded_by: when a memory is merged into a super, point old → new
--   so we can audit the consolidation chain. NULL = active leaf.

ALTER TABLE agent_memories
  ADD COLUMN IF NOT EXISTS consolidation_level INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS superseded_by UUID REFERENCES agent_memories(id) ON DELETE SET NULL;

-- Sweeper helper: find leaves at level=N that haven't been consolidated
CREATE INDEX IF NOT EXISTS idx_agent_memories_active_leaves
  ON agent_memories(agent_id, user_id, consolidation_level)
  WHERE status = 'active' AND superseded_by IS NULL;

COMMENT ON COLUMN agent_memories.consolidation_level IS
  'Wave 5d M2.B: number of merge passes — 0=original, 1+ = consolidated super-memory';
COMMENT ON COLUMN agent_memories.superseded_by IS
  'When this row was merged into a super, points at the super''s id. Audit trail; not used for retrieval.';
