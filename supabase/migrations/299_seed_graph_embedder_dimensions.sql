-- Seed the shared memory-embedder dimension into system_settings so the admin
-- Memory panel can set it (and GraphMemoryConfig.from_settings can read it)
-- without editing prod compose env. Mirrors the 291 seed pattern.
--
-- Default '1536' = Qwen3-Embedding-4B native width, which is also Honcho's
-- current pgvector column dimension — so this seed is a no-op for the running
-- system, it just makes the value explicit and admin-controllable.
--
-- The admin PUT caps this at 2000 (pgvector HNSW index limit). Stored as a
-- jsonb string via to_jsonb(...::text), like the other graph_* keys, so asyncpg
-- round-trips it cleanly (the read path int-parses it).

INSERT INTO system_settings (key, value, description)
VALUES
  ('graph_embedder_dimensions', to_jsonb('1536'::text),
   'Shared memory embedder output dimension (sizes the vector index; admin-capped at 2000 for pgvector HNSW)')
ON CONFLICT (key) DO NOTHING;
