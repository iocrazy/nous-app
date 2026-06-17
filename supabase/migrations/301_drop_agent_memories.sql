-- 301 — drop the home-grown L1 memory table `agent_memories`.
--
-- The L1 memory layer (extractor → writer → agent_memories → retriever
-- recall) has been removed from the backend. Durable memory now lives
-- entirely in the two remaining layers:
--   * Honcho  (user model)        — external service, no local table
--   * Graphiti (temporal graph)   — FalkorDB, no local relational table
--
-- FK audit: the ONLY foreign key referencing `agent_memories` is its own
-- self-referential `superseded_by → agent_memories(id)` (added in mig 189).
-- No other table references it, so the table can be dropped cleanly.
-- CASCADE is used defensively to also drop that self-FK, the indexes
-- (idx_agent_memories_*), and the check constraints created across migs
-- 147 / 189 / and later, without requiring them to be dropped first.
--
-- Created by: feature/l1-memory-removal (L1 retirement).

DROP TABLE IF EXISTS public.agent_memories CASCADE;
