-- Seed graph-memory (Graphiti) config keys into system_settings so the gate +
-- extractor/embedder provider can be set from the admin Memory panel instead
-- of editing prod compose env (which Watchtower does not reload).
--
-- All empty / disabled by default: the gate stays OFF until an admin sets a
-- FalkorDB host AND flips graph_memory_enabled (and a FalkorDB backend exists).
-- Values are stored as jsonb STRINGS via to_jsonb(...::text) so asyncpg
-- deserializes them back to clean Python strings (a bare 'true' would come
-- back as a JSON boolean; GraphMemoryConfig.from_settings str()-coerces, but
-- uniform strings keep the admin UI round-trip simple).
--
-- SECURITY: graph_extractor_api_key / graph_embedder_api_key live here, so
-- system_settings must stay admin/service-role-only — never exposed to the
-- anon PostgREST surface. The admin GET endpoint masks these.

INSERT INTO system_settings (key, value, description)
VALUES
  ('graph_memory_enabled',     to_jsonb('false'::text),           'Graphiti graph-memory master gate (true/false)'),
  ('graph_falkordb_host',      to_jsonb(''::text),                'FalkorDB host (graph backend); empty = gate inoperative'),
  ('graph_falkordb_port',      to_jsonb('6379'::text),            'FalkorDB port'),
  ('graph_falkordb_database',  to_jsonb('mediahub_memory'::text), 'FalkorDB graph name'),
  ('graph_extractor_base_url', to_jsonb(''::text),                'Graphiti extractor LLM base_url (OpenAI-compatible)'),
  ('graph_extractor_api_key',  to_jsonb(''::text),                'Graphiti extractor LLM api_key (admin/service-role only)'),
  ('graph_extractor_model',    to_jsonb(''::text),                'Graphiti extractor LLM model id'),
  ('graph_embedder_base_url',  to_jsonb(''::text),                'Graphiti embedder base_url (OpenAI-compatible)'),
  ('graph_embedder_api_key',   to_jsonb(''::text),                'Graphiti embedder api_key (admin/service-role only)'),
  ('graph_embedder_model',     to_jsonb(''::text),                'Graphiti embedder model id')
ON CONFLICT (key) DO NOTHING;
