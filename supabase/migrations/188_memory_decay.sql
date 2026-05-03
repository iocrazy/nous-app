-- Wave 5d (M2.A): memory decay + archival for agent_memories.
--
-- Adds two columns + an archival lifecycle so old unused memories
-- don't pile up forever:
--   - status: active / archived / superseded (TEXT enum-by-CHECK)
--   - archived_at: when status flipped to non-active
--
-- The decay SCORE itself (e^(-Δt/half_life)) is computed at retrieve
-- time — not stored — because half_life is a per-call tunable. We
-- store only what's needed: created_at + last_recalled_at + reinforce
-- count (already exist).
--
-- Weekly archival job (deferred to D2 sweeper) flips memories whose
-- decay_score < 0.05 to status='archived'. Archived memories are
-- excluded from retrieval but kept for forensic / explain-why-recall.

ALTER TABLE agent_memories
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active',
  ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'agent_memories_status_check'
  ) THEN
    ALTER TABLE agent_memories
      ADD CONSTRAINT agent_memories_status_check
      CHECK (status IN ('active', 'archived', 'superseded'));
  END IF;
END$$;

-- Index for the retrieval path: active rows only, partial keeps it tiny.
CREATE INDEX IF NOT EXISTS idx_agent_memories_active_namespace
  ON agent_memories(agent_id, user_id, scope, created_at DESC)
  WHERE status = 'active' AND user_id IS NOT NULL;

-- Index for the archival sweeper: pending decay candidates.
CREATE INDEX IF NOT EXISTS idx_agent_memories_active_for_archival
  ON agent_memories(last_recalled_at NULLS FIRST, created_at)
  WHERE status = 'active';

COMMENT ON COLUMN agent_memories.status IS
  'Lifecycle: active (retrievable) / archived (decayed, hidden from retrieval) / superseded (replaced by a newer contradictory memory — M2.C).';
COMMENT ON COLUMN agent_memories.archived_at IS
  'When status transitioned to non-active. Used by sweeper for re-evaluation cooldown.';
