-- Wave 5d (M2.D): expand extracted_from CHECK to include 'active_call'.
--
-- Today the constraint allows {user_msg, assistant_msg} — the
-- harvest paths from chat history. M2.D adds an "agent calls remember()
-- tool mid-turn" path; the writer tags those rows with extracted_from
-- = 'active_call' for telemetry + extraction-pipeline routing.

DO $$
BEGIN
  -- Drop + re-add since check constraints don't support ALTER
  IF EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'agent_memories_extracted_from_check'
  ) THEN
    ALTER TABLE agent_memories
      DROP CONSTRAINT agent_memories_extracted_from_check;
  END IF;
  ALTER TABLE agent_memories
    ADD CONSTRAINT agent_memories_extracted_from_check
    CHECK (extracted_from IS NULL OR extracted_from IN
      ('user_msg', 'assistant_msg', 'active_call'));
END$$;

COMMENT ON COLUMN agent_memories.extracted_from IS
  'Source channel: user_msg / assistant_msg (passive harvest) / active_call (agent invoked remember() tool). Wave 5d M2.D adds active_call.';
